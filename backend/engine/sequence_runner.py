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


class ScreenVerificationError(Exception):
    """Raised when screen verification fails — triggers step retry/recovery."""
    pass


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
        self.current_screen = "home"  # "home" | "shop" | "attack" | "search" | "unknown"
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
            "current_screen": self.current_screen,
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

        _last_failed_step_idx = -1

        while self._running:
            self.state = "RUNNING"
            steps = farming_steps if current_mode == "farming" else upgrade_steps

            i = 0
            while i < len(steps) and self._running:
                step = steps[i]
                try:
                    await self._execute_step(step, adb)
                    _last_failed_step_idx = -1  # success resets failure tracker
                    i += 1
                except ScreenVerificationError:
                    logger.warning("Screen verification failed for step %s", step.step_type)
                    if i == _last_failed_step_idx:
                        if step.step_type != "upgrade_execute":
                            logger.error("Step %s failed twice — returning home and skipping",
                                         step.step_type)
                            await self._do_return_home(adb)
                        else:
                            logger.error("Step %s failed twice — skipping (already at home)",
                                         step.step_type)
                        _last_failed_step_idx = -1
                        i += 1
                    else:
                        logger.info("Retrying step %s", step.step_type)
                        _last_failed_step_idx = i
                        await asyncio.sleep(1)
                except Exception as e:
                    logger.error("Step %s failed: %s", step.step_type, e)
                    if i == _last_failed_step_idx:
                        if step.step_type != "upgrade_execute":
                            logger.error("Step %s failed twice — returning home and skipping",
                                         step.step_type)
                            await self._do_return_home(adb)
                        else:
                            logger.error("Step %s failed twice — skipping (already at home)",
                                         step.step_type)
                        _last_failed_step_idx = -1
                        i += 1
                    else:
                        logger.info("Retrying step %s", step.step_type)
                        _last_failed_step_idx = i
                        await asyncio.sleep(1)

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
            self.current_screen = "search"
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

            # Check for misclick into Shop screen before tapping Next.
            # Reuses the same screenshot taken for loot OCR above.
            await self._verify_search_screen(adb, screen)

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

        # Verify we are on the attack/battle screen before deploying.
        # If cards cannot be detected, we are on the wrong screen.
        cards = await self._detect_cards(adb)
        if not cards:
            logger.warning("Attack screen verification failed — recovery triggered")
            raise ScreenVerificationError("Not on attack screen (no cards detected)")

        self.current_screen = "attack"
        logger.info("Deploying troops...")

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

    # ── Screen Verification & Recovery ─────────────────────────────

    async def _verify_search_screen(self, adb, screen: bytes | None = None):
        """Check for Shop screen during search loop. If detected, close it.

        Called before each Next-tap in _do_search. Returns True if search
        should continue, raises ScreenVerificationError if recovery fails.
        """
        from backend.vision.matching import match_template
        from pathlib import Path

        tpl_shop = Path(self._TPL_DIR) / "icon_shop_tab.png"
        tpl_close = Path(self._TPL_DIR) / "btn_shop_close.png"

        if not tpl_shop.exists():
            return  # template not yet captured — skip verification

        cap = screen or await adb.screencap()
        if not cap:
            return

        result = match_template(cap, str(tpl_shop), threshold=0.6)
        if result is None:
            return  # not on Shop screen, all good

        self.current_screen = "shop"
        logger.warning("Shop detected during search — attempting to close")

        if tpl_close.exists():
            close_pos = match_template(cap, str(tpl_close), threshold=0.5)
            if close_pos:
                await human_tap(adb, *close_pos, sigma=5)
                await human_delay(1.0, 2.0)
                logger.info("Shop closed via X button template match")
                return

        # Fallback: hardcoded tap near top-right corner
        logger.info("Shop close button not matched — using fallback tap")
        await human_tap(adb, 1180, 80, sigma=20)
        await human_delay(1.5, 2.5)
        # Recheck if Shop is gone
        cap2 = await adb.screencap()
        if cap2 and match_template(cap2, str(tpl_shop), threshold=0.6):
            logger.error("Failed to close Shop screen after fallback")
            raise ScreenVerificationError("Shop screen persisted after close attempt")

    async def _verify_home_screen(self, adb, max_taps: int = 10):
        """After return-home, dismiss any event/popup covering the village.

        Uses the Attack button as a home-screen landmark. If not visible,
        taps randomly to dismiss the popup. Raises ScreenVerificationError
        if the home screen cannot be reached.
        """
        from backend.vision.matching import match_template
        from pathlib import Path

        tpl_attack = Path(self._TPL_DIR) / "btn_attack.png"
        if not tpl_attack.exists():
            return  # template not yet captured — skip verification

        for attempt in range(max_taps + 1):
            cap = await adb.screencap()
            if not cap:
                continue

            result = match_template(cap, str(tpl_attack), threshold=0.6)
            if result is not None:
                self.current_screen = "home"
                if attempt > 0:
                    logger.info("Home screen confirmed after %d dismissal tap(s)", attempt)
                return  # home screen visible

            if attempt < max_taps:
                # Random tap to dismiss popup — most CoC popups close on any tap
                tx = random.randint(400, 900)
                ty = random.randint(300, 600)
                logger.warning("Home screen blocked — dismissing (tap %d/%d at %d,%d)",
                               attempt + 1, max_taps, tx, ty)
                await human_tap(adb, tx, ty, sigma=30)
                await human_delay(1.0, 2.0)

        # Last resort: try tapping return-home again
        logger.warning("Popup persisted after %d taps — retrying return-home", max_taps)
        await human_tap(adb, 640, 650, sigma=15)
        await human_delay(2.0, 3.0)
        cap = await adb.screencap()
        if cap and match_template(cap, str(tpl_attack), threshold=0.6):
            self.current_screen = "home"
            logger.info("Home screen confirmed after return-home retry")
            return

        logger.error("Failed to reach home screen after %d dismissal attempts", max_taps)
        # Don't raise — better to continue with wrong screen than hang

    # ── End Screen Verification ────────────────────────────────────

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

    def _match_in_region(self, region_gray, tpl_path, offset_x, offset_y, threshold):
        """Match a template within a pre-cropped region. Returns screen coords or None."""
        from pathlib import Path
        from backend.vision.matching import match_template as _mt
        tpl_file = Path(tpl_path)
        if not tpl_file.exists():
            return None
        tpl = cv2.imread(str(tpl_file), cv2.IMREAD_UNCHANGED)
        if tpl is None:
            return None
        th, tw = tpl.shape[:2]
        mask = tpl[:, :, 3] if len(tpl.shape) == 3 and tpl.shape[2] == 4 else None
        tpl_gray = cv2.cvtColor(tpl[:, :, :3], cv2.COLOR_BGR2GRAY) if mask is not None else tpl
        if mask is not None:
            result = cv2.matchTemplate(region_gray, tpl_gray, cv2.TM_CCOEFF_NORMED, mask=mask)
        else:
            result = cv2.matchTemplate(region_gray, tpl_gray, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val < threshold:
            return None
        return (max_loc[0] + tw // 2 + offset_x, max_loc[1] + th // 2 + offset_y)

    # Template paths for upgrade flow
    _TPL_DIR = "storage/templates"
    _TPL_SUGGESTION = f"{_TPL_DIR}/btn_upgrade_suggestion.png"
    _TPL_HAMMER = f"{_TPL_DIR}/btn_upgrade_hammer.png"
    _TPL_COG = f"{_TPL_DIR}/cog_upgrade.png"
    _TPL_UPGRADE_BTNS = [f"{_TPL_DIR}/hammer_upgrade-removebg.png",
                         f"{_TPL_DIR}/cog_upgrade-removebg.png"]
    _TPL_CONFIRM = [f"{_TPL_DIR}/btn_upgrade_confirm_1.png",
                    f"{_TPL_DIR}/btn_upgrade_confirm_2.png"]
    _TPL_SHOP_ARROW = f"{_TPL_DIR}/shop_arrow_green.png"
    _TPL_DEPLOY_CHECKMARK = f"{_TPL_DIR}/btn_deploy_checkmark.png"

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

    def _read_resource_safe(self, screen, roi, label: str) -> int | None:
        """Read a resource number with guard against OCR misreads.

        Uses capped height (25px) to avoid reading collector/label text
        that appears above and below the number.
        Rejects impossible values (>200M).
        Returns None on misread (caller falls back to previous value).
        """
        from backend.vision.ocr import read_number
        MAX_RESOURCE = 200_000_000

        if not roi:
            return 0

        val = read_number(screen, roi.x_pos, roi.y_pos, roi.width, roi.height,
                          roi_name=roi.roi_name) or 0

        if val > MAX_RESOURCE:
            # Try to auto-fix: OCR often reads "31" as "317" — try removing
            # one '7' from the first 3 digit positions
            s = str(val)
            for i in range(min(3, len(s))):
                if s[i] == '7':
                    fixed = int(s[:i] + s[i+1:])
                    if fixed <= MAX_RESOURCE:
                        logger.info("%s OCR auto-fixed: %d → %d", label, val, fixed)
                        return fixed
            logger.warning("%s OCR misread: %d, falling back to previous", label, val)
            return None  # None = misread signal, caller uses prev_val

        return val

    async def read_current_resources(self):
        """Take a screenshot and OCR own-base resources into instance variables.

        Gold and elixir use calibrated DB ROI (positions don't change).
        Dark elixir is detected via template matching — if icon not found
        (TH < 7), it returns 0. Gems uses DB ROI.
        """
        adb = adb_manager
        if not adb.is_connected:
            return
        screen = await adb.screencap()
        if not screen:
            return

        from backend.vision.ocr import read_number
        from backend.vision.matching import match_template

        with get_session() as session:
            gold_roi = session.query(RoiTemplate).filter_by(roi_name="own_gold_number").first()
            elixir_roi = session.query(RoiTemplate).filter_by(roi_name="own_elixir_number").first()
            de_roi = session.query(RoiTemplate).filter_by(roi_name="own_dark_elixir_number").first()
            gems_roi = session.query(RoiTemplate).filter_by(roi_name="own_gems_number").first()

        self.current_gold = self._read_resource_safe(
            screen, gold_roi, "gold")
        if self.current_gold is None:
            self.current_gold = self._prev_gold

        self.current_elixir = self._read_resource_safe(
            screen, elixir_roi, "elixir")
        if self.current_elixir is None:
            self.current_elixir = self._prev_elixir

        # Dark elixir only exists at TH >= 7. When absent, gems occupies that position.
        de_tpl = f"{self._TPL_DIR}/icon_dark_elixir.png"
        if match_template(screen, de_tpl, threshold=0.5):
            self.current_dark_elixir = (de_roi and read_number(screen, de_roi.x_pos, de_roi.y_pos,
                de_roi.width, de_roi.height, roi_name=de_roi.roi_name)) or 0
            self.current_gems = (gems_roi and read_number(screen, gems_roi.x_pos, gems_roi.y_pos,
                gems_roi.width, gems_roi.height, roi_name=gems_roi.roi_name)) or 0
        else:
            self.current_dark_elixir = 0
            self.current_gems = (de_roi and read_number(screen, de_roi.x_pos, de_roi.y_pos,
                de_roi.width, de_roi.height, roi_name=de_roi.roi_name)) or 0

        logger.info("Resources read: G=%d E=%d DE=%d Gems=%d",
            self.current_gold, self.current_elixir, self.current_dark_elixir, self.current_gems)

        # Gold maxed detection
        if self.current_gold > 0 and self.current_gold == self._prev_gold:
            self._gold_stable += 1
            if self._gold_stable >= 2:
                self._gold_max = True
        else:
            self._gold_stable = 0
            self._gold_max = False
        self._prev_gold = self.current_gold

        # Elixir maxed detection
        if self.current_elixir > 0 and self.current_elixir == self._prev_elixir:
            self._elixir_stable += 1
            if self._elixir_stable >= 2:
                self._elixir_max = True
        else:
            self._elixir_stable = 0
            self._elixir_max = False
        self._prev_elixir = self.current_elixir

        # Dark elixir maxed detection
        if self.current_dark_elixir > 0 and self.current_dark_elixir == self._prev_dark_elixir:
            self._de_stable += 1
            if self._de_stable >= 2:
                self._dark_elixir_max = True
        else:
            self._de_stable = 0
            self._dark_elixir_max = False
        self._prev_dark_elixir = self.current_dark_elixir

        if self._gold_max or self._elixir_max or self._dark_elixir_max:
            logger.info("Maxed resources: Gold=%s Elixir=%s DE=%s",
                self._gold_max, self._elixir_max, self._dark_elixir_max)

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
        """Determine whether to farm or upgrade. Returns 'farming' or 'upgrade'.

        Opens the builder menu and checks for a "Suggested Upgrade" header
        via template matching. No AI call — just image matching + OCR fallback.
        """
        await human_delay(0.5, 1.0)

        screen = await adb.screencap()
        if not screen:
            return "farming"

        builders = self._read_builder_count(screen)
        if builders < 1:
            logger.info("No free builders — farming mode")
            return "farming"

        with get_session() as session:
            menu_roi = session.query(RoiTemplate).filter_by(roi_name="builder_menu").first()
        if not menu_roi:
            return "farming"
        menu_cx = menu_roi.x_pos + menu_roi.width // 2
        menu_cy = menu_roi.y_pos + menu_roi.height // 2

        # Open builder menu
        await human_tap(adb, menu_cx, menu_cy, sigma=3)
        await human_delay(1.5, 2.5)

        screen2 = await adb.screencap()
        if not screen2:
            await human_tap(adb, menu_cx, menu_cy, sigma=3)
            return "farming"

        # Check for suggested upgrade via OCR text detection
        from backend.vision.ocr import find_text

        if find_text(screen2, "Suggested"):
            logger.info("Suggested upgrade found — switching to upgrade")
            await human_tap(adb, menu_cx, menu_cy, sigma=3)  # close menu
            self._upgrade_target = {
                "resources": {"gold": 0, "elixir": 0, "dark_elixir": 0}}
            return "upgrade"

        # No suggested upgrades — check if builders are busy
        if find_text(screen2, "progress"):
            logger.info("Builders busy (upgrades in progress) — farming mode")
        else:
            logger.info("No suggested upgrades — farming mode")

        await human_tap(adb, menu_cx, menu_cy, sigma=3)  # close menu
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
        """Execute an upgrade: open builder menu, select building, confirm.
        Uses template matching throughout — no AI calls."""
        from backend.db.database import get_session
        from backend.db.models import RoiTemplate

        self.state = "UPGRADING"

        with get_session() as session:
            menu_roi = session.query(RoiTemplate).filter_by(roi_name="builder_menu").first()
        if not menu_roi:
            logger.warning("builder_menu ROI not calibrated")
            self._upgrade_target = None
            return
        menu_cx = menu_roi.x_pos + menu_roi.width // 2
        menu_cy = menu_roi.y_pos + menu_roi.height // 2

        # Phase 1: Open menu, OCR find "Suggested" header, tap first row
        await human_tap(adb, menu_cx, menu_cy, sigma=3)
        await human_delay(1.5, 2.5)

        screen = await adb.screencap()
        if not screen:
            self._upgrade_target = None
            return

        from backend.vision.ocr import find_text

        suggested_pos = find_text(screen, "Suggested")
        if not suggested_pos:
            logger.info("No suggested upgrade found in menu")
            await human_tap(adb, menu_cx, menu_cy, sigma=3)
            self._upgrade_target = None
            return

        tap_x = suggested_pos[0]
        tap_y = suggested_pos[1] + 60  # first upgrade row below header
        logger.info("OCR found Suggested at (%d,%d) — tapping row at (%d,%d)",
                     suggested_pos[0], suggested_pos[1], tap_x, tap_y)
        await human_tap(adb, tap_x, tap_y, sigma=5)
        await human_delay(1.0, 1.5)

        # Check if Shop opened (new building, not existing upgrade)
        screen2 = await adb.screencap()
        if screen2:
            from pathlib import Path
            from backend.vision.matching import match_template
            if match_template(screen2, str(Path(self._TPL_DIR) / "icon_shop_tab.png"),
                              threshold=0.6):
                logger.info("New building detected — entering purchase flow")
                result = await self._do_new_building_purchase(adb)
                if result:
                    logger.info("New building purchased and deployed")
                    self._upgrade_target = None
                    return
                else:
                    # Try to close Shop and return home
                    close_pos = match_template(
                        screen2, str(Path(self._TPL_DIR) / "btn_shop_close.png"),
                        threshold=0.5,
                    )
                    if close_pos:
                        await human_tap(adb, close_pos[0], close_pos[1], sigma=5)
                    else:
                        await human_tap(adb, 1180, 80, sigma=20)
                    await human_delay(0.5, 1.5)
                    self._upgrade_target = None
                    return

        # Normal flow — close builder menu, find hammer/cog
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

        # Phase 3: Find "Confirm" text via OCR (reliable across event skins)
        screen = await adb.screencap()
        if not screen:
            self._upgrade_target = None
            return
        from backend.vision.ocr import find_text
        confirm_pos = find_text(screen, "Confirm")
        if confirm_pos:
            # Tap slightly below the text center (button extends downward)
            await human_tap(adb, confirm_pos[0], confirm_pos[1] + 20, sigma=5)
            await human_delay(0.5, 1.0)
        else:
            # Fallback: template matching, then calibrated ROI
            confirm_pos = None
            for tpl_path in self._TPL_CONFIRM:
                confirm_pos = match_template(screen, tpl_path, threshold=0.5)
                if confirm_pos:
                    break
            if confirm_pos:
                await human_tap(adb, confirm_pos[0], confirm_pos[1], sigma=3)
                await human_delay(0.5, 1.0)
            else:
                # Last fallback: calibrated ROI
                with get_session() as session:
                    confirm_roi = session.query(RoiTemplate).filter_by(
                        roi_name="btn_confirm_upgrade").first()
                if confirm_roi:
                    cx = confirm_roi.x_pos + confirm_roi.width // 2
                    cy = confirm_roi.y_pos + confirm_roi.height // 2
                    await human_tap(adb, cx, cy, sigma=5)
                    await human_delay(0.5, 1.0)
                else:
                    logger.warning("Confirm button not found (OCR + template + ROI)")
                    self._upgrade_target = None
                    return

        # Phase 3b: Dismiss hero hall confirmation popup if present
        # Hero upgrades have a second confirmation layer that needs dismissal
        await human_delay(0.5, 1.0)
        with get_session() as session:
            close_roi = session.query(RoiTemplate).filter_by(roi_name="btn_close_universal").first()
        if close_roi:
            cx = close_roi.x_pos + close_roi.width // 2
            cy = close_roi.y_pos + close_roi.height // 2
            await human_tap(adb, cx, cy, sigma=5)
            logger.info("Tapped btn_close_universal at (%d,%d)", cx, cy)

        logger.info("Upgrade confirmed — resources spent")
        self._upgrade_target = None

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
        sug_pos = match_template(screen, self._TPL_SUGGESTION, threshold=0.40)
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
        hammer_pos = match_template(screen, self._TPL_HAMMER, threshold=0.40)
        if not hammer_pos:
            logger.warning("Hammer upgrade button not found — trying to dismiss popup first")
            # Maybe still on suggestion panel — tap away and retry
            await human_tap(adb, 640, 360, sigma=40)
            await human_delay(0.5, 1.0)
            screen = await adb.screencap()
            if screen:
                hammer_pos = match_template(screen, self._TPL_HAMMER, threshold=0.40)
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
            confirm_pos = match_template(screen, tpl_path, threshold=0.40)
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

    async def _do_new_building_purchase(self, adb) -> bool:
        """Purchase a new building from Shop and deploy it on the home village.

        Called when Shop screen is detected after tapping a Suggested row.
        Returns True on success, False on failure (caller handles retry).

        Phases:
          A) Find & tap the green arrow on the highlighted building card
          B) Verify we left the Shop (entered deploy screen)
          C) Drag until building base is green (valid placement), max 20 attempts
          D) Tap checkmark to confirm placement
        """
        from backend.vision.matching import match_template
        from pathlib import Path

        logger.info("=== New Building Purchase & Deploy ===")
        self.current_screen = "shop"

        # --- Phase A: Find and tap the arrow in Shop (AI only, full-res) ---
        screen = await adb.screencap()
        if not screen:
            return False

        arrow_pos = None
        ai = self._get_ai_client()
        if not ai or not ai.available:
            logger.warning("AI not available — cannot find Shop arrow")
            return False

        arrow_prompt = (
            "You are a Clash of Clans bot assistant. This is a 1280x720 screenshot of "
            "the Shop/Build menu. A new building is highlighted for purchase. "
            "On the highlighted card there is a GREEN DOWNWARD-POINTING ARROW (⤓) "
            "overlaid on the building picture. This arrow indicates the building can be "
            "bought. Find the EXACT pixel center of that green arrow.\n"
            "Return ONLY valid JSON:\n"
            '{"buildings": [{"name": "Arrow", "x": 640, "y": 360, "cost": 0, '
            '"resource": "gold"}]}\n'
            "IMPORTANT: x must be 0-1279, y must be 0-719. The arrow is in the "
            "main building list area (roughly y=200-500), not in the top tab bar "
            "or bottom UI."
        )

        # Try AI at full resolution (small arrows need pixel-level detail)
        result = ai.analyze_screenshot(screen, prompt_override=arrow_prompt, full_res=True)
        if result and len(result) > 0:
            arrow_pos = (result[0]["x"], result[0]["y"])
            logger.info("Arrow found via AI at (%d,%d)", arrow_pos[0], arrow_pos[1])

        # Validate coordinates are reasonable
        if arrow_pos and (arrow_pos[0] < 100 or arrow_pos[0] > 1200
                          or arrow_pos[1] < 150 or arrow_pos[1] > 550):
            logger.warning("AI arrow coords unreasonable (%d,%d) — retrying",
                           arrow_pos[0], arrow_pos[1])
            # Retry once with explicit coordinate hint
            result2 = ai.analyze_screenshot(
                screen,
                prompt_override=arrow_prompt + (
                    "\n\nPREVIOUS ATTEMPT RETURNED INVALID COORDINATES. "
                    "The green arrow is in the center area of the screen, roughly "
                    "x=500-700, y=250-450. Look for the card with the green down-arrow "
                    "on the building image."
                ),
                full_res=True,
            )
            if result2 and len(result2) > 0:
                arrow_pos = (result2[0]["x"], result2[0]["y"])
                logger.info("Arrow retry at (%d,%d)", arrow_pos[0], arrow_pos[1])
            else:
                arrow_pos = None

        if not arrow_pos:
            logger.warning("Could not find Shop arrow — purchase aborted")
            return False

        await human_tap(adb, arrow_pos[0], arrow_pos[1], sigma=5)
        await human_delay(1.5, 2.5)

        # --- Phase B: Verify we left Shop (entered deploy screen) ---
        screen = await adb.screencap()
        if not screen:
            return False

        shop_still = match_template(
            screen, str(Path(self._TPL_DIR) / "icon_shop_tab.png"), threshold=0.6,
        )
        if shop_still:
            # Still in Shop — arrow tap missed, retry once with AI if template was used
            logger.warning("Still in Shop after tapping arrow — retrying with AI")
            ai = self._get_ai_client()
            if ai and ai.available:
                arrow_prompt = (
                    "You are a Clash of Clans bot assistant. Analyze this Shop screenshot "
                    "(1280x720). Find the green arrow on the highlighted building card. "
                    "Return ONLY valid JSON:\n"
                    '{"buildings": [{"name": "Arrow", "x": 640, "y": 360, "cost": 0, '
                    '"resource": "gold"}]}'
                )
                result = ai.analyze_screenshot(screen, prompt_override=arrow_prompt)
                if result and len(result) > 0:
                    await human_tap(adb, result[0]["x"], result[0]["y"], sigma=5)
                    await human_delay(1.5, 2.5)
                    screen = await adb.screencap()
                    if screen and match_template(
                        screen, str(Path(self._TPL_DIR) / "icon_shop_tab.png"),
                        threshold=0.6,
                    ):
                        logger.warning("Still in Shop after retry — giving up")
                        return False
                else:
                    logger.warning("AI could not find arrow on retry")
                    return False
            else:
                return False

        logger.info("Deploy screen confirmed — building ready to place")
        self.current_screen = "home"

        # DEBUG: Save full deploy screen for analysis
        _dbg = await adb.screencap()
        if _dbg:
            import os as _os, cv2 as _cv2, numpy as _np
            _os.makedirs("storage/debug", exist_ok=True)
            _arr = _np.frombuffer(_dbg, _np.uint8)
            _img = _cv2.imdecode(_arr, _cv2.IMREAD_COLOR)
            _cv2.imwrite("storage/debug/deploy_full.png", _img)
            logger.info("Debug: deploy_full.png saved")

        # --- Phase C: HSV red detection for X button → calculate CK offset ---
        # Red (X button) is highly distinctive against green grass — near-zero false positives.
        # CoC deploy bar: X (red, left) always adjacent to checkmark (green, right) by ~48px.
        valid = False
        ck_pos = None
        _HSV_OFFSET = 48  # px from X center to checkmark center at 1280x720

        for attempt in range(1, 21):
            screen = await adb.screencap()
            if not screen:
                break

            import numpy as _np, cv2 as _cv2
            _nparr = _np.frombuffer(screen, _np.uint8)
            _img = _cv2.imdecode(_nparr, _cv2.IMREAD_COLOR)
            img_h, img_w = _img.shape[:2]

            # Search bottom 35% only — deploy buttons at very bottom
            search_y1 = img_h * 65 // 100
            bottom_region = _img[search_y1:img_h, 0:img_w]
            hsv = _cv2.cvtColor(bottom_region, _cv2.COLOR_BGR2HSV)

            # Red mask: H in [0-10] or [170-180], S >= 150, V >= 150
            lower_r1 = _np.array([0, 150, 150])
            upper_r1 = _np.array([10, 255, 255])
            lower_r2 = _np.array([170, 150, 150])
            upper_r2 = _np.array([180, 255, 255])
            red_mask = _cv2.inRange(hsv, lower_r1, upper_r1) | _cv2.inRange(
                hsv, lower_r2, upper_r2,
            )

            contours, _ = _cv2.findContours(
                red_mask, _cv2.RETR_TREE, _cv2.CHAIN_APPROX_SIMPLE,
            )

            # Find best X candidate: area 150-2000, x < 70% screen width
            x_pos = None
            for cnt in sorted(contours, key=_cv2.contourArea, reverse=True):
                area = _cv2.contourArea(cnt)
                if area < 150 or area > 2000:
                    continue
                bx, by, bw, bh = _cv2.boundingRect(cnt)
                cx = bx + bw // 2
                cy = by + bh // 2 + search_y1
                if cx < img_w * 70 // 100:  # reject far-right UI
                    x_pos = (cx, cy)
                    break

            if not x_pos:
                logger.info("No red X button in bottom region #%d", attempt)
            else:
                ck_x = x_pos[0] + _HSV_OFFSET
                ck_y = x_pos[1]
                # Verify checkmark region has green (building must be in valid spot)
                half = 10
                x1_c = max(0, ck_x - half)
                y1_c = max(0, ck_y - half)
                x2_c = min(img_w, ck_x + half)
                y2_c = min(img_h, ck_y + half)
                ck_roi = _img[y1_c:y2_c, x1_c:x2_c]
                gm = ((ck_roi[:, :, 1].astype(int) > 150) &
                      (ck_roi[:, :, 1].astype(int) > ck_roi[:, :, 2].astype(int) * 1.1))
                if _np.count_nonzero(gm) >= 5:
                    ck_pos = (ck_x, ck_y)
                    logger.info("FOUND X=(%d,%d) CK=(%d,%d) via HSV #%d",
                                x_pos[0], x_pos[1], ck_x, ck_y, attempt)
                    valid = True
                    break
                else:
                    logger.info("X at (%d,%d) but no green at CK — keep dragging #%d",
                                x_pos[0], x_pos[1], attempt)

            dx = random.randint(-250, 250)
            dy = random.randint(-200, 200)
            await adb.swipe(640, 400, 640 + dx, 400 + dy, duration_ms=200)
            await human_delay(0.4, 0.6)

        if not valid:
            logger.warning("Failed to place building after 20 attempts — cancelling")
            await human_tap(adb, 100, 650, sigma=10)
            await human_delay(0.5, 1.0)
            return False

        # --- Phase D: Confirm placement ---
        # Re-verify X button visible on fresh screenshot via HSV
        import os as _os
        _os.makedirs("storage/debug", exist_ok=True)
        debug_screen = await adb.screencap()
        if debug_screen and ck_pos:
            import numpy as _np, cv2 as _cv2
            _dbg = _cv2.imdecode(_np.frombuffer(debug_screen, _np.uint8), _cv2.IMREAD_COLOR)
            dbg_h, dbg_w = _dbg.shape[:2]
            dbg_y1 = dbg_h * 65 // 100
            dbg_hsv = _cv2.cvtColor(
                _dbg[dbg_y1:dbg_h, 0:dbg_w], _cv2.COLOR_BGR2HSV,
            )
            dbg_red = _cv2.inRange(dbg_hsv, lower_r1, upper_r1) | _cv2.inRange(
                dbg_hsv, lower_r2, upper_r2,
            )
            dbg_cnts, _ = _cv2.findContours(
                dbg_red, _cv2.RETR_TREE, _cv2.CHAIN_APPROX_SIMPLE,
            )

            x_reverify = None
            for cnt in sorted(dbg_cnts, key=_cv2.contourArea, reverse=True):
                area = _cv2.contourArea(cnt)
                if area < 150 or area > 2000:
                    continue
                bx, by, bw, bh = _cv2.boundingRect(cnt)
                cx = bx + bw // 2
                cy = by + bh // 2 + dbg_y1
                if cx < dbg_w * 70 // 100:
                    x_reverify = (cx, cy)
                    break

            if x_reverify:
                ck_rx = x_reverify[0] + _HSV_OFFSET
                ck_ry = x_reverify[1]
                dist = int(_np.sqrt((ck_pos[0] - ck_rx) ** 2
                                    + (ck_pos[1] - ck_ry) ** 2))
                if dist > 20:
                    logger.info("CK moved %dpx: (%d,%d) → (%d,%d) — using new position",
                                dist, ck_pos[0], ck_pos[1], ck_rx, ck_ry)
                else:
                    logger.info("CK verified at (%d,%d) — stable via HSV",
                                ck_pos[0], ck_pos[1])
                ck_pos = (ck_rx, ck_ry)
            else:
                logger.warning("X not found on fresh screenshot — cancelling")
                return False

            _cv2.circle(_dbg, ck_pos, 12, (0, 255, 0), 3)
            _cv2.line(_dbg, (ck_pos[0] - 20, ck_pos[1]), (ck_pos[0] + 20, ck_pos[1]), (0, 255, 0), 2)
            _cv2.line(_dbg, (ck_pos[0], ck_pos[1] - 20), (ck_pos[0], ck_pos[1] + 20), (0, 255, 0), 2)
            _cv2.imwrite("storage/debug/checkmark_tap_target.png", _dbg)
            logger.info("Debug: checkmark target saved at (%d,%d)", ck_pos[0], ck_pos[1])

        await human_tap(adb, ck_pos[0], ck_pos[1], sigma=3)
        await human_delay(1.0, 2.0)

        logger.info("New building purchased and deployed successfully")
        self._upgrade_target = None
        return True

    async def _poll_countdown_then_return(self, adb):
        """Poll countdown timer; when it disappears, battle is over."""
        from backend.vision.matching import match_template
        TPL_COUNTDOWN = f"{self._TPL_DIR}/btn_countdown.png"

        logger.info("Watching countdown timer...")
        while self._running:
            await asyncio.sleep(3)
            screen = await adb.screencap()
            if screen and not match_template(screen, TPL_COUNTDOWN, threshold=0.40):
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

        # Dismiss any event/gacha popup that appeared during transition
        await self._verify_home_screen(adb)

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

        self.current_screen = "home"
        self.state = "RUNNING"


sequence_runner = SequenceRunner()
