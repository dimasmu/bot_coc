"""Sequence runner: executes user-defined step sequences."""

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass

import cv2
import numpy as np

from backend.adb.manager import adb_manager
from backend.humanize import human_tap, human_delay
from backend.vision.ocr import read_number
from backend.db.database import get_session
from backend.db.models import RoiTemplate, Config, AttackLog

logger = logging.getLogger(__name__)


@dataclass
class CardResult:
    """Result of reading a card's count badge via OCR."""
    count: int | None   # None=hero, 0=empty, >0=count available
    has_badge: bool      # True if badge detected (even if OCR misses number)


class SequenceRunner:
    def __init__(self):
        self._running = False
        self._task: asyncio.Task | None = None
        self.state = "STOPPED"
        self.gold_earned = 0
        self.elixir_earned = 0
        self.dark_elixir_earned = 0
        self.raids_completed = 0

    @property
    def is_running(self):
        return self._running

    def get_status_dict(self):
        return {
            "state": self.state,
            "running": self._running,
            "gold_earned": self.gold_earned,
            "elixir_earned": self.elixir_earned,
            "dark_elixir_earned": self.dark_elixir_earned,
            "raids_completed": self.raids_completed,
        }

    async def start(self, sequence_id: int | None = None):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(sequence_id))

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.state = "STOPPED"

    async def _run(self, sequence_id: int | None = None):
        adb = adb_manager
        if not adb.is_connected:
            await adb.connect()

        with get_session() as session:
            from backend.db.models import BotSequence, SequenceStep
            if sequence_id:
                seq = session.query(BotSequence).get(sequence_id)
            else:
                seq = session.query(BotSequence).filter_by(is_active=True).first()
            if not seq:
                logger.error("No sequence found")
                return
            steps = session.query(SequenceStep).filter_by(sequence_id=seq.id).order_by(SequenceStep.step_order).all()

        while self._running:
            self.state = "RUNNING"
            for step in steps:
                if not self._running:
                    break
                try:
                    await self._execute_step(step, adb)
                except Exception as e:
                    logger.error("Step %s failed: %s", step.step_type, e)
                    await asyncio.sleep(2)

    async def _execute_step(self, step, adb):
        stype = step.step_type

        if stype == "tap":
            if step.roi_name:
                with get_session() as s:
                    roi = s.query(RoiTemplate).filter_by(roi_name=step.roi_name).first()
                if roi:
                    cx = roi.x_pos + roi.width // 2
                    cy = roi.y_pos + roi.height // 2
                    logger.info("Tapping %s at (%d, %d)", step.roi_name, cx, cy)
                    await human_tap(adb, cx, cy, sigma=5)
                else:
                    logger.warning("ROI '%s' not found", step.roi_name)

        elif stype == "wait":
            dur = step.duration or 1.0
            logger.info("Waiting %.1fs", dur)
            await asyncio.sleep(dur)

        elif stype == "search":
            await self._do_search(step, adb)

        elif stype == "attack":
            await self._do_attack(step, adb)

        elif stype == "return_home":
            await self._do_return_home(adb)

    async def _do_search(self, step, adb):
        from backend.db.database import get_session
        from backend.db.models import RoiTemplate, Config

        with get_session() as session:
            gold_roi = session.query(RoiTemplate).filter_by(roi_name="gold_number").first()
            elixir_roi = session.query(RoiTemplate).filter_by(roi_name="elixir_number").first()
            next_roi = session.query(RoiTemplate).filter_by(roi_name="btn_next").first()
            min_gold_th = session.query(Config).filter_by(key="min_gold_threshold").first()
            min_elixir_th = session.query(Config).filter_by(key="min_elixir_threshold").first()
            min_gold = int(min_gold_th.value) if min_gold_th else 300000
            min_elixir = int(min_elixir_th.value) if min_elixir_th else 300000

        config = json.loads(step.config_json) if step.config_json else {}
        max_searches = config.get("max_searches", 30)
        logger.info("Search thresholds: G>=%d, E>=%d, max=%d", min_gold, min_elixir, max_searches)

        search_count = 0
        while search_count < max_searches and self._running:
            search_count += 1
            await human_delay(1.0, 2.0)

            screen = await adb.screencap()
            if not screen:
                continue

            gold_val = gold_roi and read_number(screen, gold_roi.x_pos, gold_roi.y_pos, gold_roi.width, gold_roi.height, roi_name=gold_roi.roi_name)
            elixir_val = elixir_roi and read_number(screen, elixir_roi.x_pos, elixir_roi.y_pos, elixir_roi.width, elixir_roi.height, roi_name=elixir_roi.roi_name)

            self.state = f"SEARCHING #{search_count}"
            logger.info("Search #%d: Gold=%s Elixir=%s", search_count, gold_val, elixir_val)

            if gold_val and gold_val >= min_gold and elixir_val and elixir_val >= min_elixir:
                logger.info("Target found! G=%d E=%d", gold_val, elixir_val)
                self._last_search_count = search_count
                return

            if next_roi:
                nx = next_roi.x_pos + next_roi.width // 2
                ny = next_roi.y_pos + next_roi.height // 2
                logger.info("Tapping Next at (%d,%d)", nx, ny)
                await human_tap(adb, nx, ny, sigma=5)
            else:
                logger.info("Tapping Next at fallback (1069,490)")
                await human_tap(adb, 1069, 490, sigma=15)

            await human_delay(1.2, 3.5)

        self._last_search_count = search_count

    async def _detect_cards(self, adb):
        """Scan the troop bar to find all active cards. Returns list of {x, y}."""
        screen = await adb.screencap()
        if not screen:
            return []

        nparr = np.frombuffer(screen, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
        h, w = img.shape

        bar_top = max(0, h - 180)
        bar = img[bar_top:h, 0:w]

        cards = []
        scan_positions = [60 + i * 80 for i in range(14)]
        card_w, card_h = 60, 85

        for scan_x in scan_positions:
            x1 = max(0, scan_x - card_w // 2)
            y1 = bar.shape[0] - card_h
            x2 = min(w, x1 + card_w)
            if x2 <= x1:
                continue

            region = bar[y1:, x1:x2]
            if np.std(region) < 35:
                continue

            cards.append({
                "x": scan_x,
                "y": bar_top + y1 + card_h // 2,
                "card_top": bar_top + y1,
            })
            logger.info("  Card at X=%d", scan_x)

        return cards

    async def _card_is_grey(self, adb, card):
        """Check if card is grey/depleted. Uses saturation (not brightness)."""
        try:
            screen = await adb.screencap()
            if not screen:
                return False
            nparr = np.frombuffer(screen, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            hx = max(0, card["x"] - 25)
            hy = max(0, card["card_top"] + 15)
            roi = img[hy:hy + 55, hx:hx + 50]
            if roi.size == 0:
                return False
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            v_mean = float(np.mean(hsv[:, :, 2]))  # brightness
            s_mean = float(np.mean(hsv[:, :, 1]))  # saturation
            # Grey/depleted cards are desaturated (low S) but may still be bright
            is_grey = s_mean < 50
            logger.info("  Card X=%d V=%.1f S=%.1f grey=%s", card["x"], v_mean, s_mean, is_grey)
            return is_grey
        except Exception:
            return True

    async def _do_attack(self, step, adb):
        config = json.loads(step.config_json) if step.config_json else {}
        duration = config.get("duration", 180)

        self.state = "ATTACKING"
        logger.info("Deploying troops...")

        cards = await self._detect_cards(adb)

        if not cards:
            logger.warning("No cards detected, cannot deploy")
            await asyncio.sleep(duration)
            return

        logger.info("Detected %d cards, deploying...", len(cards))

        # Hardcoded deploy zone centers (from user's calibrated deploy_1..deploy_9)
        DEPLOY_ZONES = [
            (70, 345), (160, 257), (333, 129),
            (457, 36), (850, 32), (994, 137),
            (1162, 262), (1092, 485), (964, 552),
        ]        # Track which cards have been confirmed grey — once grey, never re-check
        grey_cards = set()

        end_time = time.time() + duration
        while self._running and time.time() < end_time:
            active = [i for i in range(len(cards)) if i not in grey_cards]
            if not active:
                logger.info("All cards grey — deployment complete")
                break

            for i in active:
                if not self._running or time.time() >= end_time:
                    break

                card = cards[i]
                cx, cy = card["x"], card["y"]

                # Only check grey for cards we haven't confirmed yet
                if i not in grey_cards and await self._card_is_grey(adb, card):
                    grey_cards.add(i)
                    logger.info("  Card %d at (%d,%d): turned grey, flagged", i + 1, cx, cy)
                    continue

                await human_tap(adb, cx, cy, sigma=2)
                logger.info("  Card %d at (%d,%d): 50 taps", i + 1, cx, cy)
                for _ in range(50):
                    zx, zy = random.choice(DEPLOY_ZONES)
                    await human_tap(adb, zx, zy, sigma=8)
                    await human_delay(0.005, 0.01)

        remaining = max(0, end_time - time.time())
        if remaining > 0:
            logger.info("Waiting for battle (%.0fs)...", remaining)
            await asyncio.sleep(remaining)

    async def _do_return_home(self, adb):
        self.state = "RETURNING"
        logger.info("Returning home...")
        await human_delay(3.0, 5.0)

        with get_session() as session:
            home_roi = session.query(RoiTemplate).filter_by(roi_name="btn_return_home").first()

        if home_roi:
            cx = home_roi.x_pos + home_roi.width // 2
            cy = home_roi.y_pos + home_roi.height // 2
            await human_tap(adb, cx, cy, sigma=10)
        else:
            await human_tap(adb, 640, 650, sigma=20)

        await human_delay(3.0, 5.0)

        # Log attack
        self.raids_completed += 1
        try:
            with get_session() as session:
                from backend.db.models import AttackLog
                log = AttackLog(
                    gold_earned=self.gold_earned,
                    elixir_earned=self.elixir_earned,
                    dark_elixir_earned=self.dark_elixir_earned,
                    search_count=getattr(self, "_last_search_count", 1),
                )
                session.add(log)
                session.commit()
        except Exception:
            pass

        self.state = "RUNNING"


sequence_runner = SequenceRunner()
