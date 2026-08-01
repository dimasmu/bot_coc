"""Sequence runner: executes user-defined step sequences."""

import asyncio
import json
import logging
import random
import time

import cv2
import numpy as np

from backend.adb.manager import adb_manager
from backend.humanize import human_tap, human_delay
from backend.vision.ocr import read_number
from backend.db.database import get_session
from backend.db.models import RoiTemplate, Config, AttackLog

logger = logging.getLogger(__name__)


class SequenceRunner:
    def __init__(self):
        self._running = False
        self._task: asyncio.Task | None = None
        self.state = "STOPPED"
        self.gold_earned = 0
        self.elixir_earned = 0
        self.dark_elixir_earned = 0
        self.raids_completed = 0
        self._upgrade_item = None  # holds selected UpgradeQueue item between check/execute

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
        elif stype == "upgrade_check":
            await self._do_upgrade_check(adb)
        elif stype == "upgrade_execute":
            await self._do_upgrade_execute(adb)

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

    # Card center X positions calibrated via card_1..card_11 ROIs (1280x720)
    _CARD_X_POSITIONS = [139, 236, 347, 451, 547, 644, 740, 849, 946, 1044, 1138]

    async def _detect_cards(self, adb):
        """Scan the troop bar to find all active cards using calibrated positions.

        Card positions hardcoded from card_1..card_11 calibrations.
        Filters empty slots (dashed border, transparent, no card texture).
        """
        screen = await adb.screencap()
        if not screen:
            return []

        nparr = np.frombuffer(screen, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
        h, w = img.shape

        bar_top = max(0, h - 180)
        bar = img[bar_top:h, 0:w]

        cards = []
        card_w, card_h = 60, 85

        for scan_x in self._CARD_X_POSITIONS:
            x1 = max(0, scan_x - card_w // 2)
            y1 = bar.shape[0] - card_h
            x2 = min(w, x1 + card_w)
            if x2 <= x1:
                continue

            region = bar[y1:, x1:x2]
            # Empty slots: transparent, dashed border → very low texture
            if np.std(region) < 20:
                continue

            cards.append({
                "x": scan_x,
                "y": bar_top + y1 + card_h // 2,
                "card_top": bar_top + y1,
            })
            logger.info("  Card at X=%d", scan_x)

        return cards

    async def _is_card_depleted(self, adb, card) -> bool:
        """Check if a card is grey/depleted using multi-method color analysis.

        Methods (any one can trigger depletion):
        1. HSV saturation — grey pixels have S≈0.
           Cards with mean saturation < 35 are likely depleted.
        2. Colourful pixel ratio — R≈G≈B pixels are grey.
           Cards with <20% colourful pixels are likely depleted.
        """
        try:
            screen = await adb.screencap()
            if not screen:
                return False
            nparr = np.frombuffer(screen, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            h_img, w_img = img.shape[:2]

            # Card region: 60x85px
            x1 = max(0, card["x"] - 30)
            y1 = max(0, card["card_top"])
            x2 = min(w_img, x1 + 60)
            y2 = min(h_img, y1 + 85)
            if x2 <= x1 or y2 <= y1:
                return False

            roi = img[y1:y2, x1:x2]

            # --- Method 1: HSV saturation (S channel) ---
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            s_channel = hsv[:, :, 1].astype(np.float32)
            s_mean = float(np.mean(s_channel))
            s_low = np.count_nonzero(s_channel < 40)
            s_total = s_channel.size
            s_low_pct = s_low / s_total

            # --- Method 2: Colourful pixel ratio (RGB spread) ---
            bgr = roi.astype(np.int32)
            b, g, r = cv2.split(bgr)
            ch_range = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
            colourful = np.count_nonzero(ch_range >= 15)
            colourful_pct = colourful / ch_range.size

            # --- Decision: depleted if ANY method says so ---
            grey_by_sat_low = s_low_pct > 0.70      # >70% low-saturation pixels
            grey_by_sat_mean = s_mean < 100          # mean saturation dropped (semi-grey)
            grey_by_colour = colourful_pct < 0.20    # <20% colourful pixels
            is_depleted = grey_by_sat_low or grey_by_sat_mean or grey_by_colour

            logger.info(
                "  Card X=%d | S-mean=%.1f S-low=%.1f%% colourful=%.1f%% | "
                "grey_satL=%s grey_satM=%s grey_col=%s → depleted=%s",
                card["x"], s_mean, s_low_pct * 100, colourful_pct * 100,
                grey_by_sat_low, grey_by_sat_mean, grey_by_colour, is_depleted,
            )
            return bool(is_depleted)
        except Exception:
            return False

    async def _do_attack(self, step, adb):
        config = json.loads(step.config_json) if step.config_json else {}
        duration = config.get("duration", 180) + 5  # +5s safety margin

        self.state = "ATTACKING"
        logger.info("Deploying troops...")

        cards = await self._detect_cards(adb)

        if not cards:
            logger.warning("No cards detected, cannot deploy")
            await asyncio.sleep(duration)
            return

        # Hardcoded deploy zone centers (from user's calibrated deploy_1..deploy_9)
        DEPLOY_ZONES = [
            (70, 345), (160, 257), (333, 129),
            (457, 36), (850, 32), (994, 137),
            (1162, 262), (1092, 485), (964, 552),
        ]

        depleted = set()       # cards confirmed grey/depleted

        logger.info("Detected %d cards", len(cards))

        end_time = time.time() + duration
        while self._running and time.time() < end_time:
            active = [i for i in range(len(cards)) if i not in depleted]
            if not active:
                logger.info("All cards depleted — deployment complete")
                break

            for i in active:
                if not self._running or time.time() >= end_time:
                    break

                card = cards[i]
                cx, cy = card["x"], card["y"]

                # Check if card is depleted (grey)
                if await self._is_card_depleted(adb, card):
                    depleted.add(i)
                    logger.info("  Card %d at X=%d: depleted, skipping",
                                i + 1, card["x"])
                    continue

                # Deploy
                await human_tap(adb, cx, cy, sigma=2)
                logger.info("  Card %d at (%d,%d): deploying", i + 1, cx, cy)
                for _ in range(10):
                    zx, zy = random.choice(DEPLOY_ZONES)
                    await human_tap(adb, zx, zy, sigma=8)
                    await human_delay(0.005, 0.01)
                await human_delay(0.05, 0.15)

        # Reserve ~8s for _do_return_home so it runs within the 185s window
        remaining = max(0, end_time - time.time() - 8)
        if remaining > 0:
            logger.info("Waiting for battle (%.0fs, return home in ~8s)...", remaining)
            await asyncio.sleep(remaining)

    async def _do_upgrade_check(self, adb):
        """Check upgrade queue for affordable upgrades with available builders."""
        from backend.db.database import get_session
        from backend.db.models import UpgradeQueue, RoiTemplate
        from backend.vision.ocr import read_number

        self.state = "UPGRADE_CHECK"
        logger.info("Checking upgrade queue...")

        screen = await adb.screencap()
        if not screen:
            self._upgrade_item = None
            return

        with get_session() as session:
            items = session.query(UpgradeQueue).filter_by(
                status="PENDING", upgrade_type="building"
            ).order_by(UpgradeQueue.priority_order).all()

        if not items:
            logger.info("No PENDING building upgrades in queue")
            self._upgrade_item = None
            return

        # Read resources
        with get_session() as session:
            gold_roi = session.query(RoiTemplate).filter_by(roi_name="gold_number").first()
            elixir_roi = session.query(RoiTemplate).filter_by(roi_name="elixir_number").first()
            de_roi = session.query(RoiTemplate).filter_by(roi_name="dark_elixir_number").first()

        gold = gold_roi and read_number(screen, gold_roi.x_pos, gold_roi.y_pos,
                                        gold_roi.width, gold_roi.height,
                                        roi_name=gold_roi.roi_name)
        elixir = elixir_roi and read_number(screen, elixir_roi.x_pos, elixir_roi.y_pos,
                                            elixir_roi.width, elixir_roi.height,
                                            roi_name=elixir_roi.roi_name)
        de = de_roi and read_number(screen, de_roi.x_pos, de_roi.y_pos,
                                    de_roi.width, de_roi.height,
                                    roi_name=de_roi.roi_name)
        resources = {"gold": gold or 0, "elixir": elixir or 0, "dark_elixir": de or 0}
        logger.info("Resources: G=%d E=%d DE=%d", resources["gold"], resources["elixir"], resources["dark_elixir"])

        # Check builder count
        with get_session() as session:
            builder_roi = session.query(RoiTemplate).filter_by(roi_name="builder_count").first()

        free_builders = 5  # default
        if builder_roi:
            bc = read_number(screen, builder_roi.x_pos, builder_roi.y_pos,
                            builder_roi.width, builder_roi.height,
                            roi_name="builder_count")
            if bc is not None:
                free_builders = bc
        logger.info("Free builders: %d", free_builders)

        if free_builders < 1:
            logger.info("No free builders — skipping upgrades")
            self._upgrade_item = None
            return

        # Find first affordable item
        for item in items:
            res_val = resources.get(item.resource_type, 0)
            if item.cost and item.cost > 0 and res_val < item.cost:
                logger.info("  %s lvl %d: need %d %s, have %d — skip",
                             item.name, item.target_level, item.cost,
                             item.resource_type, res_val)
                continue
            # Affordable (or cost unknown — will OCR in upgrade_execute)
            self._upgrade_item = item
            logger.info("Selected: %s lvl %d (cost=%s %s)",
                         item.name, item.target_level,
                         item.cost or "?", item.resource_type)
            return

        logger.info("No affordable upgrades found")
        self._upgrade_item = None

    async def _do_upgrade_execute(self, adb):
        """Execute the upgrade selected by _do_upgrade_check."""
        from backend.db.database import get_session
        from backend.db.models import UpgradeQueue, RoiTemplate
        from backend.vision.ocr import read_number
        from datetime import datetime

        if not getattr(self, "_upgrade_item", None):
            logger.info("No upgrade item selected — skipping")
            return

        item = self._upgrade_item
        self.state = "UPGRADING"
        logger.info("Executing upgrade: %s lvl %d", item.name, item.target_level)

        screen = await adb.screencap()
        if not screen:
            return

        # Tap builder menu button
        with get_session() as session:
            menu_roi = session.query(RoiTemplate).filter_by(roi_name="builder_menu").first()

        if menu_roi:
            cx = menu_roi.x_pos + menu_roi.width // 2
            cy = menu_roi.y_pos + menu_roi.height // 2
            await human_tap(adb, cx, cy, sigma=5)
            await human_delay(1.0, 2.0)

        # Read cost from upgrade screen
        with get_session() as session:
            cost_roi = session.query(RoiTemplate).filter_by(roi_name="upgrade_cost").first()

        detected_cost = None
        if cost_roi:
            screen2 = await adb.screencap()
            if screen2:
                detected_cost = read_number(screen2, cost_roi.x_pos, cost_roi.y_pos,
                                            cost_roi.width, cost_roi.height,
                                            roi_name="upgrade_cost")
        logger.info("Detected upgrade cost: %s", detected_cost)

        # Tap upgrade button
        with get_session() as session:
            btn_upgrade = session.query(RoiTemplate).filter_by(roi_name="btn_upgrade").first()
        if btn_upgrade:
            cx = btn_upgrade.x_pos + btn_upgrade.width // 2
            cy = btn_upgrade.y_pos + btn_upgrade.height // 2
            await human_tap(adb, cx, cy, sigma=3)
            await human_delay(0.5, 1.0)

        # Tap confirm button
        with get_session() as session:
            btn_confirm = session.query(RoiTemplate).filter_by(roi_name="btn_upgrade_confirm").first()
        if btn_confirm:
            cx = btn_confirm.x_pos + btn_confirm.width // 2
            cy = btn_confirm.y_pos + btn_confirm.height // 2
            await human_tap(adb, cx, cy, sigma=3)
            await human_delay(0.5, 1.0)

        # Update DB
        with get_session() as session:
            db_item = session.query(UpgradeQueue).get(item.id)
            if db_item:
                db_item.status = "IN_PROGRESS"
                db_item.started_at = datetime.utcnow()
                if detected_cost:
                    db_item.cost = detected_cost
                session.commit()

        logger.info("Upgrade started: %s lvl %d (cost=%d)",
                     item.name, item.target_level, detected_cost or 0)
        self._upgrade_item = None
        await human_delay(1.0, 2.0)

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
