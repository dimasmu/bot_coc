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
from sqlmodel import select
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
        self.current_gold = 0
        self.current_elixir = 0
        self.current_dark_elixir = 0
        self.current_gems = 0
        self._upgrade_target = None  # {name, x, y, cost, resource} from AI
        self._loop_mode = ""  # "farming" or "upgrade"
        self._ai_client = None  # lazy-init DashScope client
        self._gold_max = False
        self._elixir_max = False
        self._dark_elixir_max = False
        self._prev_gold = 0
        self._prev_elixir = 0
        self._prev_dark_elixir = 0
        self._gold_stable = 0
        self._elixir_stable = 0
        self._de_stable = 0

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
            "current_gold": self.current_gold,
            "current_elixir": self.current_elixir,
            "current_dark_elixir": self.current_dark_elixir,
            "current_gems": self.current_gems,
            "loop_mode": self._loop_mode,
            "gold_max": self._gold_max,
            "elixir_max": self._elixir_max,
            "dark_elixir_max": self._dark_elixir_max,
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

            farming_seq = session.exec(
                select(BotSequence).where(BotSequence.name == "Farming Loop")
            ).first()
            upgrade_seq = session.exec(
                select(BotSequence).where(BotSequence.name == "Upgrade Loop")
            ).first()

            if not farming_seq or not upgrade_seq:
                logger.error("Required sequences not found (Farming Loop + Upgrade Loop)")
                return

            farming_steps = session.exec(
                select(SequenceStep).where(SequenceStep.sequence_id == farming_seq.id)
                .order_by(SequenceStep.step_order)
            ).all()
            upgrade_steps = session.exec(
                select(SequenceStep).where(SequenceStep.sequence_id == upgrade_seq.id)
                .order_by(SequenceStep.step_order)
            ).all()

        # Respect user's sequence selection for initial mode
        if sequence_id and sequence_id == upgrade_seq.id:
            current_mode = "upgrade"
        else:
            current_mode = "farming"
        self._loop_mode = current_mode

        while self._running:
            self.state = "RUNNING"
            steps = farming_steps if current_mode == "farming" else upgrade_steps

            for step in steps:
                if not self._running:
                    break
                try:
                    await self._execute_step(step, adb)
                except Exception as e:
                    logger.error("Step %s failed: %s", step.step_type, e)
                    await asyncio.sleep(2)

            if self._running:
                try:
                    if current_mode != "farming":
                        current_mode = await self._evaluate_mode(adb)
                    else:
                        # Lightweight: OCR builder count only, no AI
                        await human_delay(0.5, 1.0)
                        screen = await adb.screencap()
                        if screen:
                            builders = self._read_builder_count(screen)
                            logger.info("Builder count: %d", builders)
                            if builders > 0:
                                current_mode = "upgrade"
                                logger.info("Builder free — switching to upgrade loop")
                        else:
                            logger.warning("Screencap failed during builder check")
                    self._loop_mode = current_mode
                    await self.read_current_resources()
                except Exception as e:
                    logger.error("Post-loop evaluate failed: %s", e)
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

            if gold_val is None and elixir_val is None:
                logger.info("OCR failed for both — attacking blind")
                self._last_search_count = search_count
                return

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

        Starts from 11 hardcoded card_1..card_11 positions, then extends
        left (card_0, card_-1, ...) and right (card_12, card_13, ...)
        dynamically as long as non-depleted cards are found.
        """
        screen = await adb.screencap()
        if not screen:
            return []

        nparr = np.frombuffer(screen, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
        h, w = img.shape

        bar_top = max(0, h - 180)
        bar = img[bar_top:h, 0:w]
        card_w, card_h = 60, 85
        spacing = (self._CARD_X_POSITIONS[-1] - self._CARD_X_POSITIONS[0]) // (len(self._CARD_X_POSITIONS) - 1)

        cards = []
        detected = set()

        def _check_slot(scan_x):
            """Check if there's a card at scan_x. Returns True if card found."""
            if scan_x < 0 or scan_x >= w:
                return False
            x1 = max(0, scan_x - card_w // 2)
            y1 = bar.shape[0] - card_h
            x2 = min(w, x1 + card_w)
            if x2 <= x1:
                return False
            region = bar[y1:, x1:x2]
            if np.std(region) < 20:
                return False
            if scan_x in detected:
                return False
            detected.add(scan_x)
            cards.append({"x": scan_x, "y": bar_top + y1 + card_h // 2, "card_top": bar_top + y1})
            logger.info("  Card at X=%d", scan_x)
            return True

        # Scan core 11 positions
        for scan_x in self._CARD_X_POSITIONS:
            _check_slot(scan_x)

        # Extend LEFT: check for cards before card_1
        left_x = self._CARD_X_POSITIONS[0] - spacing
        for _ in range(3):  # up to 3 extra cards left
            if left_x < 0:
                break
            if not _check_slot(left_x):
                break  # stop at first empty slot
            left_x -= spacing

        # Extend RIGHT: check for cards after card_11
        right_x = self._CARD_X_POSITIONS[-1] + spacing
        for _ in range(3):  # up to 3 extra cards right
            if right_x >= w:
                break
            if not _check_slot(right_x):
                break  # stop at first empty slot
            right_x += spacing

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
            logger.warning("No cards detected — returning home")
            return

        # Hardcoded deploy zone centers (from user's calibrated deploy_1..deploy_9)
        DEPLOY_ZONES = [
            (70, 345), (160, 257), (333, 129),
            (457, 36), (850, 32), (994, 137),
            (1162, 262), (1092, 485), (964, 552),
        ]

        depleted = set()       # cards confirmed grey/depleted

        logger.info("Detected %d cards", len(cards))

        # Timer starts at first deploy, not function entry
        end_time = None     # set on first actual deployment
        deployed = False     # True once at least one card is deployed

        while self._running and (end_time is None or time.time() < end_time):
            active = [i for i in range(len(cards)) if i not in depleted]
            if not active:
                logger.info("All cards depleted — deployment complete")
                break

            for i in active:
                if not self._running or (end_time is not None and time.time() >= end_time):
                    break

                card = cards[i]
                cx, cy = card["x"], card["y"]

                # Check if card is depleted (grey)
                if await self._is_card_depleted(adb, card):
                    depleted.add(i)
                    logger.info("  Card %d at X=%d: depleted, skipping",
                                i + 1, card["x"])
                    continue

                # Start timer on first actual deploy
                if end_time is None:
                    end_time = time.time() + duration
                    logger.info("Attack timer started: %ds from first deploy", duration)

                # Deploy
                deployed = True
                await human_tap(adb, cx, cy, sigma=2)
                logger.info("  Card %d at (%d,%d): deploying", i + 1, cx, cy)
                for _ in range(10):
                    zx, zy = random.choice(DEPLOY_ZONES)
                    await human_tap(adb, zx, zy, sigma=8)
                    await human_delay(0.005, 0.01)
                await human_delay(0.05, 0.15)

        if deployed:
            logger.info("Battle started — polling countdown timer...")
            await self._poll_countdown_then_return(adb)
        else:
            logger.info("No cards deployed — returning home")

    async def _do_upgrade_check(self, adb):
        """Read builder count and resources, store for _do_upgrade_execute."""
        self.state = "UPGRADE_CHECK"
        logger.info("Checking builders and resources for upgrade...")
        screen = await adb.screencap()
        if not screen:
            self._upgrade_target = None
            return

        builders = self._read_builder_count(screen)
        if builders < 1:
            logger.info("No free builders — skipping upgrade")
            self._upgrade_target = None
            return

        resources = self._read_resources(screen)
        logger.info("Builders=%d Resources: G=%d E=%d DE=%d",
                     builders, resources["gold"], resources["elixir"], resources["dark_elixir"])
        self._upgrade_target = {"resources": resources}

    # Template paths for upgrade flow
    _TPL_DIR = "storage/templates"
    _TPL_SUGGESTION = f"{_TPL_DIR}/btn_upgrade_suggestion.png"
    _TPL_HAMMER = f"{_TPL_DIR}/btn_upgrade_hammer.png"
    _TPL_COG = f"{_TPL_DIR}/cog_upgrade.png"
    _TPL_UPGRADE_BTNS = [f"{_TPL_DIR}/hammer_upgrade-removebg.png",
                         f"{_TPL_DIR}/cog_upgrade-removebg.png"]
    _TPL_CONFIRM = [f"{_TPL_DIR}/btn_upgrade_confirm_1.png",
                    f"{_TPL_DIR}/btn_upgrade_confirm_2.png"]

    def _read_resources(self, screen) -> dict:
        """OCR gold, elixir, and dark elixir from own base screen."""
        from backend.vision.ocr import read_number
        with get_session() as session:
            gold_roi = session.query(RoiTemplate).filter_by(roi_name="own_gold_number").first()
            elixir_roi = session.query(RoiTemplate).filter_by(roi_name="own_elixir_number").first()
            de_roi = session.query(RoiTemplate).filter_by(roi_name="own_dark_elixir_number").first()
        gold = gold_roi and read_number(screen, gold_roi.x_pos, gold_roi.y_pos,
                                        gold_roi.width, gold_roi.height, roi_name=gold_roi.roi_name)
        elixir = elixir_roi and read_number(screen, elixir_roi.x_pos, elixir_roi.y_pos,
                                            elixir_roi.width, elixir_roi.height, roi_name=elixir_roi.roi_name)
        de = de_roi and read_number(screen, de_roi.x_pos, de_roi.y_pos,
                                    de_roi.width, de_roi.height, roi_name=de_roi.roi_name)
        return {"gold": gold or 0, "elixir": elixir or 0, "dark_elixir": de or 0}

    async def read_current_resources(self):
        """Take a screenshot and OCR own-base resources into instance variables."""
        adb = adb_manager
        if not adb.is_connected:
            return
        screen = await adb.screencap()
        if not screen:
            return

        from backend.vision.ocr import read_number
        with get_session() as session:
            gold_roi = session.query(RoiTemplate).filter_by(roi_name="own_gold_number").first()
            elixir_roi = session.query(RoiTemplate).filter_by(roi_name="own_elixir_number").first()
            de_roi = session.query(RoiTemplate).filter_by(roi_name="own_dark_elixir_number").first()
            gems_roi = session.query(RoiTemplate).filter_by(roi_name="own_gems_number").first()

        self.current_gold = (gold_roi and read_number(screen, gold_roi.x_pos, gold_roi.y_pos,
            gold_roi.width, gold_roi.height, roi_name=gold_roi.roi_name)) or 0
        self.current_elixir = (elixir_roi and read_number(screen, elixir_roi.x_pos, elixir_roi.y_pos,
            elixir_roi.width, elixir_roi.height, roi_name=elixir_roi.roi_name)) or 0
        self.current_dark_elixir = (de_roi and read_number(screen, de_roi.x_pos, de_roi.y_pos,
            de_roi.width, de_roi.height, roi_name=de_roi.roi_name)) or 0
        self.current_gems = (gems_roi and read_number(screen, gems_roi.x_pos, gems_roi.y_pos,
            gems_roi.width, gems_roi.height, roi_name=gems_roi.roi_name)) or 0

        logger.info("Resources read: G=%d E=%d DE=%d Gems=%d",
            self.current_gold, self.current_elixir, self.current_dark_elixir, self.current_gems)

    def _read_builder_count(self, screen) -> int:
        """OCR free builder count from format 'X/Y' (e.g. '2/5'). Defaults to 1 if misread."""
        import re
        from backend.vision.ocr import read_raw_text, read_number

        with get_session() as session:
            builder_roi = session.query(RoiTemplate).filter_by(roi_name="builder_count").first()
        if not builder_roi:
            return 1

        # Primary: read full text (e.g. "2/5") via EasyOCR without digit filter
        text = read_raw_text(screen, builder_roi.x_pos, builder_roi.y_pos,
                             builder_roi.width, builder_roi.height)
        m = re.search(r'(\d+)\s*/\s*\d+', text) if text else None
        if m:
            bc = int(m.group(1))
            if bc > 6:
                logger.warning("Builder count OCR returned %d (recalibrate ROI)", bc)
                return 1
            logger.info("Builder count OCR: '%s' -> %d", text, bc)
            return bc

        # Fallback: legacy digit-only OCR
        bc = read_number(screen, builder_roi.x_pos, builder_roi.y_pos,
                         builder_roi.width, builder_roi.height, roi_name="builder_count")
        if bc is None:
            return 1
        if bc > 6:
            logger.warning("Builder count OCR returned %d (recalibrate ROI)", bc)
            return 1
        return bc

    async def _evaluate_mode(self, adb) -> str:
        """Determine whether to farm or upgrade. Returns 'farming' or 'upgrade'."""
        # Wait for screen to stabilize after return_home
        await human_delay(0.5, 1.0)

        screen = await adb.screencap()
        if not screen:
            return "farming"

        builders = self._read_builder_count(screen)
        if builders < 1:
            logger.info("No free builders — farming mode")
            return "farming"

        client = self._get_ai_client()
        if not client.available:
            logger.info("AI not available — farming mode")
            return "farming"

        with get_session() as session:
            menu_roi = session.query(RoiTemplate).filter_by(roi_name="builder_menu").first()
        if not menu_roi:
            return "farming"
        menu_cx = menu_roi.x_pos + menu_roi.width // 2
        menu_cy = menu_roi.y_pos + menu_roi.height // 2
        await human_tap(adb, menu_cx, menu_cy, sigma=3)
        await human_delay(1.5, 2.5)

        screen2 = await adb.screencap()
        if not screen2:
            await human_tap(adb, menu_cx, menu_cy, sigma=3)
            return "farming"

        ai_buildings = client.analyze_screenshot(screen2)
        await human_tap(adb, menu_cx, menu_cy, sigma=3)
        await human_delay(0.5, 1.0)

        if not ai_buildings:
            logger.info("No upgradable buildings found — farming mode")
            return "farming"

        # Read resources from clean base screen (same method as search)
        screen3 = await adb.screencap()
        resources = self._read_resources(screen3) if screen3 else {"gold": 0, "elixir": 0, "dark_elixir": 0}

        logger.info("Resources: G=%d E=%d DE=%d, builders=%d",
                     resources["gold"], resources["elixir"],
                     resources["dark_elixir"], builders)

        cheapest = ai_buildings[0]
        res_val = resources.get(cheapest.get("resource", "gold"), 0)
        cost = cheapest.get("cost", 0)
        if res_val >= cost and cost > 0:
            logger.info("Affordable upgrade: %s (%d %s) — upgrade mode",
                         cheapest["name"], cost, cheapest["resource"])
            return "upgrade"

        logger.info("Cannot afford %s (need %d %s, have %d) — farming mode",
                     cheapest["name"], cost, cheapest["resource"], res_val)
        return "farming"

    def _get_ai_client(self):
        """Lazy-init the DashScope client from DB config."""
        if self._ai_client is not None:
            return self._ai_client
        from backend.vision.ai import DashScopeClient
        try:
            with get_session() as session:
                cfg = session.query(Config).filter_by(key="dashscope_api_key").first()
            api_key = cfg.value.strip() if cfg and cfg.value else None
            self._ai_client = DashScopeClient(api_key=api_key)
            if self._ai_client.available:
                logger.info("DashScope AI client initialized")
            else:
                logger.info("DashScope API key not configured — AI disabled")
        except Exception as e:
            logger.error("Failed to initialize DashScope client: %s", e)
            self._ai_client = DashScopeClient(api_key=None)
        return self._ai_client

    def _save_debug_screenshot(self, png_bytes: bytes):
        """Save screenshot to storage/debug/ if debug flag is set."""
        if not png_bytes:
            return
        with get_session() as session:
            cfg = session.query(Config).filter_by(key="dashscope_debug_screenshots").first()
        if not cfg or cfg.value.lower() != "true":
            return
        import os
        os.makedirs("storage/debug", exist_ok=True)
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = f"storage/debug/ai_upgrade_{ts}.png"
        with open(filepath, "wb") as f:
            f.write(png_bytes)
        logger.debug("Debug screenshot saved: %s", filepath)

    async def _do_upgrade_execute(self, adb):
        """Two-AI-call: select building in menu, find upgrade button on base, confirm."""
        from backend.db.database import get_session
        from backend.db.models import RoiTemplate
        from backend.vision.ai import MENU_PROMPT, BASE_PROMPT

        target = getattr(self, "_upgrade_target", None) or {}
        resources = target.get("resources", {"gold": 0, "elixir": 0, "dark_elixir": 0})

        self.state = "UPGRADING"

        with get_session() as session:
            menu_roi = session.query(RoiTemplate).filter_by(roi_name="builder_menu").first()
        if not menu_roi:
            logger.warning("builder_menu ROI not calibrated")
            self._upgrade_target = None
            return
        menu_cx = menu_roi.x_pos + menu_roi.width // 2
        menu_cy = menu_roi.y_pos + menu_roi.height // 2

        # Phase 1: Open menu, AI find first suggested building row
        await human_tap(adb, menu_cx, menu_cy, sigma=3)
        await human_delay(1.5, 2.5)

        screen = await adb.screencap()
        if not screen:
            self._upgrade_target = None
            return
        client = self._get_ai_client()
        if not client.available:
            self._upgrade_target = None
            return
        logger.info("AI-1: scanning builder menu...")
        menu_result = client.analyze_screenshot(screen, prompt_override=MENU_PROMPT)

        if not menu_result:
            logger.info("No buildings found")
            await human_tap(adb, menu_cx, menu_cy, sigma=3)
            self._upgrade_target = None
            return

        bld = menu_result[0]
        r = bld.get("resource", "gold")
        if resources.get(r, 0) < bld.get("cost", 0):
            logger.info("Cannot afford %s (need %d %s, have %d)",
                         bld["name"], bld["cost"], r, resources.get(r, 0))
            await human_tap(adb, menu_cx, menu_cy, sigma=3)
            self._upgrade_target = None
            return

        # Tap building row then close menu
        # Tap building row using OCR to find "Suggested" text
        from backend.vision.ocr import find_text
        suggested_pos = find_text(screen, "Suggested")
        if suggested_pos:
            tap_x, tap_y = suggested_pos[0], suggested_pos[1] + 60
            logger.info("OCR found Suggested at (%d,%d), tapping at (%d,%d)",
                         suggested_pos[0], suggested_pos[1], tap_x, tap_y)
        else:
            # Fallback: use AI coordinates
            tap_x, tap_y = bld["x"], bld["y"]
            logger.info("OCR not found, using AI coords (%d,%d)", tap_x, tap_y)
        await human_tap(adb, tap_x, tap_y, sigma=5)
        await human_delay(1.0, 1.5)
        logger.info("Closing builder menu...")
        await human_tap(adb, menu_cx, menu_cy, sigma=3)
        await human_delay(1.0, 1.5)

        # Phase 2: Template match hammer OR cog
        screen = await adb.screencap()
        if not screen:
            self._upgrade_target = None
            return
        from backend.vision.matching import match_template

        # Crop to center area (skip top builder icons, bottom UI)
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(screen))
        crop = img.crop((100, 150, 1180, 600))
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        screen_crop = buf.getvalue()

        btn_pos = None
        for tpl_path in self._TPL_UPGRADE_BTNS:
            pos = match_template(screen_crop, tpl_path, threshold=0.5)
            if pos:
                # Offset back: crop started at (100, 150)
                btn_pos = (pos[0] + 100, pos[1] + 150)
                logger.info("Found upgrade btn via %s at (%d,%d)", tpl_path, btn_pos[0], btn_pos[1])
                break

        if btn_pos:
            await human_tap(adb, btn_pos[0], btn_pos[1], sigma=5)
        else:
            logger.warning("No upgrade button found (hammer/cog)")
            self._upgrade_target = None
            return
        await human_delay(1.0, 2.0)

        # Phase 3: Template match confirm button
        screen = await adb.screencap()
        if not screen:
            self._upgrade_target = None
            return
        confirm_pos = None
        for tpl_path in self._TPL_CONFIRM:
            confirm_pos = match_template(screen, tpl_path, threshold=0.5)
            if confirm_pos:
                break
        if confirm_pos:
            await human_tap(adb, confirm_pos[0], confirm_pos[1], sigma=3)
            await human_delay(0.5, 1.0)
        else:
            # Fallback to calibrated ROI
            with get_session() as session:
                confirm_roi = session.query(RoiTemplate).filter_by(roi_name="btn_confirm_upgrade").first()
            if confirm_roi:
                cx = confirm_roi.x_pos + confirm_roi.width // 2
                cy = confirm_roi.y_pos + confirm_roi.height // 2
                await human_tap(adb, cx, cy, sigma=3)
                await human_delay(0.5, 1.0)
            else:
                logger.warning("Confirm button not found")
                self._upgrade_target = None
                return

        logger.info("Upgrade started: %s (cost=%d %s)",
                     bld["name"], bld["cost"], bld["resource"])
        self._upgrade_target = None
        await human_delay(1.0, 2.0)

    async def _do_upgrade_execute_template(self, adb):
        """Fallback: execute upgrade using template matching (original logic)."""
        from backend.db.database import get_session
        from backend.db.models import RoiTemplate
        from backend.vision.ocr import read_number
        from backend.vision.matching import match_template

        if not getattr(self, "_upgrade_target", None):
            logger.info("No upgrade target — skipping")
            return

        building = self._upgrade_target
        self.state = "UPGRADING"
        logger.info("Executing upgrade: %s (cost=%d %s)",
                     building["name"], building.get("cost", 0),
                     building.get("resource", "unknown"))

        TPL_DIR = self._TPL_DIR

        # Step 1: Tap builder menu (from calibrated ROI)
        with get_session() as session:
            menu_roi = session.query(RoiTemplate).filter_by(roi_name="builder_menu").first()
        if not menu_roi:
            logger.warning("builder_menu ROI not calibrated")
            self._upgrade_target = None
            return
        menu_cx = menu_roi.x_pos + menu_roi.width // 2
        menu_cy = menu_roi.y_pos + menu_roi.height // 2
        await human_tap(adb, menu_cx, menu_cy, sigma=3)
        await human_delay(1.5, 2.5)

        # Step 2: Find "Suggested Upgrades" text → tap directly below it
        screen = await adb.screencap()
        if not screen:
            return
        sug_pos = match_template(screen, self._TPL_SUGGESTION, threshold=0.6)
        if not sug_pos:
            logger.warning("Suggested Upgrades text not found")
            # Close builder menu and bail
            await human_tap(adb, menu_cx, menu_cy, sigma=3)
            self._upgrade_target = None
            return

        # Tap ~60px below the label center to hit first suggestion item
        suggestion_tap_x = sug_pos[0]
        suggestion_tap_y = sug_pos[1] + 60
        await human_tap(adb, suggestion_tap_x, suggestion_tap_y, sigma=5)
        await human_delay(0.8, 1.5)

        # Step 3: Close builder menu to reveal the hammer button
        await human_tap(adb, menu_cx, menu_cy, sigma=3)
        await human_delay(1.0, 1.5)

        # Step 4: Find and tap the hammer button
        screen = await adb.screencap()
        if not screen:
            return
        hammer_pos = match_template(screen, self._TPL_HAMMER, threshold=0.6)
        if not hammer_pos:
            logger.warning("Hammer upgrade button not found — trying to dismiss popup first")
            # Maybe still on suggestion panel — tap away and retry
            await human_tap(adb, 640, 360, sigma=40)
            await human_delay(0.5, 1.0)
            screen = await adb.screencap()
            if screen:
                hammer_pos = match_template(screen, self._TPL_HAMMER, threshold=0.6)
        if hammer_pos:
            await human_tap(adb, hammer_pos[0], hammer_pos[1], sigma=3)
            await human_delay(1.0, 2.0)
        else:
            logger.warning("Hammer button still not found — skipping upgrade")
            self._upgrade_target = None
            return

        # Step 5: OCR upgrade cost from region below the hammer icon
        screen = await adb.screencap()
        detected_cost = None
        if screen and hammer_pos:
            # Cost text sits ~20px below the hammer icon, ~60px wide
            cost_x = hammer_pos[0] - 30
            cost_y = hammer_pos[1] + 45
            detected_cost = read_number(screen, cost_x, cost_y, 60, 25,
                                        roi_name="hammer_cost")
        logger.info("Detected upgrade cost: %s", detected_cost)

        # Step 6: Find and tap confirm button (try both variants)
        screen = await adb.screencap()
        if not screen:
            return
        confirm_pos = None
        for tpl_path in self._TPL_CONFIRM:
            confirm_pos = match_template(screen, tpl_path, threshold=0.6)
            if confirm_pos:
                logger.debug("Matched confirm template: %s", tpl_path)
                break
        if confirm_pos:
            await human_tap(adb, confirm_pos[0], confirm_pos[1], sigma=3)
            await human_delay(0.5, 1.0)
        else:
            logger.warning("Confirm button not found")
            self._upgrade_target = None
            return

        logger.info("Template upgrade: %s (cost=%d)",
                     building["name"], detected_cost or 0)
        self._upgrade_target = None
        await human_delay(1.0, 2.0)

    async def _poll_countdown_then_return(self, adb):
        """Poll countdown timer; when it disappears, battle is over."""
        from backend.vision.matching import match_template
        TPL_COUNTDOWN = f"{self._TPL_DIR}/btn_countdown.png"

        logger.info("Watching countdown timer...")
        while self._running:
            await asyncio.sleep(3)
            screen = await adb.screencap()
            if screen and not match_template(screen, TPL_COUNTDOWN, threshold=0.6):
                logger.info("Countdown disappeared — battle over")
                return

    async def _do_return_home(self, adb):
        """Tap the return-home button using calibrated ROI coordinates.

        The button position is fixed relative to 1280x720 resolution.
        Calibrate via the Calibrator tab (btn_return_home ROI).
        Falls back to hardcoded center-bottom if ROI not found.
        """
        self.state = "RETURNING"
        logger.info("Returning home — tapping return home button...")

        with get_session() as session:
            from backend.db.models import RoiTemplate
            home_roi = session.query(RoiTemplate).filter_by(roi_name="btn_return_home").first()
        if home_roi:
            cx = home_roi.x_pos + home_roi.width // 2
            cy = home_roi.y_pos + home_roi.height // 2
            logger.info("Return home at (%d,%d) via calibrated ROI", cx, cy)
        else:
            cx, cy = 640, 650
            logger.info("Return home at (%d,%d) via fallback", cx, cy)

        await human_tap(adb, cx, cy, sigma=15)
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
