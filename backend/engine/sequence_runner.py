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
from backend.vision.ocr import read_number, read_ratio
from backend.vision import analyze_confirm_button, analyze_lab_confirm_button
from sqlmodel import select
from backend.db.database import get_session
from backend.db.models import RoiTemplate, Config, AttackLog

logger = logging.getLogger(__name__)


class ScreenVerificationError(Exception):
    """Raised when screen verification fails — triggers step retry/recovery."""
    pass


class StillAtHomeError(Exception):
    """Raised when the farming loop is still on the home screen during
    search/attack — the bot never actually left home. The run loop resets
    to the upgrade loop instead of attacking blind."""
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
        self._confirm_debug_counter = 0
        # Set when all lab research rows were tried and none affordable.
        # _evaluate_mode consults it to skip re-entering the lab until the
        # next farming cycle has (hopefully) refilled resources.
        self._lab_exhausted_until = 0.0

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
                    # Pre-step: verify we're on the expected screen
                    if not await self._verify_step_screen(adb, step.step_type,
                                                          roi_name=step.roi_name):
                        logger.warning("Step %s — wrong screen, recovering home", step.step_type)
                        await self._do_return_home(adb)
                        if not await self._verify_step_screen(adb, step.step_type,
                                                              roi_name=step.roi_name):
                            logger.error("Step %s — still wrong screen after recovery, skipping",
                                         step.step_type)
                            _last_failed_step_idx = -1
                            i += 1
                            continue

                    # Farming-loop guard: search/attack must not run while
                    # the home screen is still visible — the attack taps
                    # were eaten by a popup. Reset to the upgrade loop.
                    if step.step_type in ("search", "attack") \
                            and await self._is_home_screen(adb):
                        logger.warning(
                            "Still at home before step %s — resetting to upgrade loop",
                            step.step_type,
                        )
                        current_mode = "upgrade"
                        self._loop_mode = current_mode
                        break

                    await self._execute_step(step, adb)
                    _last_failed_step_idx = -1  # success resets failure tracker
                    i += 1
                except StillAtHomeError:
                    logger.warning(
                        "Still at home during farming — resetting to upgrade loop"
                    )
                    current_mode = "upgrade"
                    self._loop_mode = current_mode
                    break
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
                            elif builders == 0:
                                current_mode = await self._evaluate_mode(adb)
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

    # ── Pre-Step Screen Verification ─────────────────────────────────

    async def _is_home_screen(self, adb, screen=None) -> bool:
        """Check if the home village screen is visible.

        Triple signal: 'Attack!' button bottom-left (OCR) + Shop button
        bottom-right (template) + no open modal (dim overlay). Post-battle
        popups (e.g. Star Bonus) leave Attack!/Shop visible underneath —
        the dim-overlay gate is what rejects them so blockers get resolved
        first.
        """
        from pathlib import Path
        from backend.vision.ocr import find_text
        from backend.vision.matching import match_template
        from backend.engine.middleware import _modal_is_open

        cap = screen or await adb.screencap()
        if not cap:
            return True  # can't verify — allow through

        # "Attack!" button is only visible on the home village screen
        # Position check: the real button is bottom-left (x<200, y>500).
        # OCR can false-match "Attack" in other UI text at x>1000.
        result = find_text(cap, "Attack")
        if not (result and result[0] < 200 and result[1] > 500):
            return False

        # Shop button bottom-right — second required signal. Uses a
        # template captured from the home screen itself (icon_shop_tab
        # is the in-shop UI tab and does NOT match the home button).
        # Restricted to the bottom-right corner so shop buttons on
        # other screens don't count.
        shop_pos = match_template(
            cap, str(Path(self._TPL_DIR) / "btn_shop_home.png"), threshold=0.8,
        )
        if not (shop_pos and shop_pos[0] >= 1050 and shop_pos[1] >= 560):
            return False

        # Third signal: no modal/popup covering the screen. A popup over
        # home keeps the corner buttons visible but dims the rest — the
        # bot must resolve the popup before treating the screen as home.
        _img = cv2.imdecode(np.frombuffer(cap, np.uint8), cv2.IMREAD_COLOR)
        if _img is not None and _modal_is_open(_img):
            return False

        return True

    async def _is_attack_screen(self, adb) -> bool:
        """Check if attack/search screen is visible (troop cards or shop icon)."""
        from backend.vision.matching import match_template
        from pathlib import Path

        cap = await adb.screencap()
        if not cap:
            return True  # can't verify — allow through

        # Try detecting troop cards (attack deploy screen)
        cards = await self._detect_cards(adb)
        if cards:
            return True

        # Fallback: Shop icon (attack search screen)
        shop_pos = match_template(
            cap, str(Path(self._TPL_DIR) / "icon_shop_tab.png"), threshold=0.6,
        )
        if shop_pos:
            return True

        return False

    async def _verify_step_screen(self, adb, step_type: str,
                                  roi_name: str | None = None) -> bool:
        """Verify the expected screen is visible before executing a step.
        Returns True if screen matches, False if recovery is needed.

        Only steps that start ON the home screen are checked against it
        (btn_attack tap at loop start, upgrade steps). Mid-attack steps
        (wait, btn_find_match/myarmy_btn_attack taps, search, attack) run
        on screens that change mid-flow — pre-checking them would derail
        the sequence; they are protected by the internal verifiers instead
        (_verify_search_screen, _detect_cards in _do_attack).
        """
        if step_type in ("return_home",):
            return True  # return_home IS the recovery — always allow

        if step_type in ("upgrade_check", "upgrade_execute"):
            if not await self._is_home_screen(adb):
                logger.warning("Step '%s': NOT on home screen", step_type)
                return False
            return True

        if step_type == "tap" and roi_name == "btn_attack":
            if not await self._is_home_screen(adb):
                logger.warning("Step 'tap btn_attack': NOT on home screen")
                return False
            return True

        return True  # wait / mid-attack taps / search / attack pass through

    # ── End Pre-Step Screen Verification ─────────────────────────────

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

            # Farming-loop guard: home buttons visible → we never entered
            # the search screen (attack taps eaten by a popup). Abort so the
            # run loop resets to the upgrade loop instead of attacking blind.
            if await self._is_home_screen(adb, screen=screen):
                logger.warning("Home screen detected during search — aborting")
                raise StillAtHomeError("Still at home during search")

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
        img_color = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        h, w = img.shape

        bar_top = max(0, h - 180)
        bar = img[bar_top:h, 0:w]
        bar_color = img_color[bar_top:h, 0:w, :]
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
            # Reject empty transparent slots showing grass behind them.
            # Check the card center (ignoring edges) for grass-green dominance.
            center_roi = bar_color[y1 + card_h // 4 : y1 + card_h * 3 // 4,
                                   x1 - scan_x + card_w // 4 : x2 - scan_x + card_w * 3 // 4]
            if center_roi.size > 0:
                hsv = cv2.cvtColor(center_roi, cv2.COLOR_BGR2HSV)
                grass_mask = cv2.inRange(
                    hsv, np.array([35, 80, 80]), np.array([85, 255, 255]),
                )
                grass_pct = np.count_nonzero(grass_mask) / grass_mask.size
                if grass_pct > 0.75:  # center is mostly grass → empty slot
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

    # ── End Screen Verification ────────────────────────────────────

    # ── Popup Blocker Resolution ────────────────────────────────────

    async def _dismiss_blockers(self, adb) -> bool:
        """Check for and dismiss any popup/event blocking the screen.

        Uses HSV color detection to find CONTINUE (green), Return Home
        (orange), or Close X (red) buttons and taps them precisely.

        Returns True if a blocker was found and dismissed.
        """
        from backend.engine.middleware import resolve_blockers
        import numpy as _np, cv2 as _cv2

        cap = await adb.screencap()
        if not cap:
            return False

        _img = _cv2.imdecode(_np.frombuffer(cap, _np.uint8), _cv2.IMREAD_COLOR)
        blocked, pos = resolve_blockers(_img)
        if blocked and pos:
            logger.info("Blocker dismissed via middleware at (%d,%d)", pos[0], pos[1])
            await human_tap(adb, pos[0], pos[1], sigma=5)
            await human_delay(1.0, 1.5)
            return True
        return False

    # ── End Popup Blocker Resolution ────────────────────────────────

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
            lab_status = self._read_lab_status(screen)
            logger.info("No free builders — lab status: %s", lab_status)
            if lab_status == "free":
                self._upgrade_target = {"type": "lab"}
            else:
                self._upgrade_target = None
            return

        resources = self._read_resources(screen)
        logger.info("Builders=%d Resources: G=%d E=%d DE=%d",
                     builders, resources["gold"], resources["elixir"], resources["dark_elixir"])
        self._upgrade_target = {"type": "building", "resources": resources}

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

    def _read_lab_status(self, screen) -> str:
        """Read lab research status from the calibrated lab_status ROI.

        OCRs the 'X/Y' badge digits only (not the icon). The number
        BEFORE the slash is the available-upgrade count — same rule as
        the builder count: 0 → nothing available, >=1 → available.

        Returns:
            'free'  — used >= 1, lab upgrade available
            'busy'  — used == 0, research in progress / nothing available
            'unknown' — OCR failed, garbage ratio, or ROI not calibrated
        """
        if not screen:
            return "unknown"

        with get_session() as session:
            lab_roi = session.query(RoiTemplate).filter_by(
                roi_name="lab_status").first()
        if not lab_roi:
            return "unknown"

        pair = read_ratio(screen, lab_roi.x_pos, lab_roi.y_pos,
                          lab_roi.width, lab_roi.height, roi_name="lab_status")
        if pair is None:
            # One retry — the small badge occasionally OCRs as garbage
            pair = read_ratio(screen, lab_roi.x_pos, lab_roi.y_pos,
                              lab_roi.width, lab_roi.height,
                              roi_name="lab_status")
        if pair is None:
            return "unknown"

        used, total = pair
        logger.info("Lab status OCR: %d/%d", used, total)
        if used > total:
            logger.warning("Lab status OCR implausible (%d/%d) — treating as unknown",
                           used, total)
            return "unknown"
        if used == 0:
            return "busy"
        return "free"

    async def _tap_close_universal(self, adb):
        """Tap the global close button (btn_close_universal ROI)."""
        with get_session() as session:
            close_roi = session.query(RoiTemplate).filter_by(
                roi_name="btn_close_universal").first()
        if close_roi:
            cx = close_roi.x_pos + close_roi.width // 2
            cy = close_roi.y_pos + close_roi.height // 2
            await human_tap(adb, cx, cy, sigma=5)
        else:
            logger.warning("btn_close_universal ROI not calibrated")

    async def _close_panel_until_gone(self, adb, max_attempts: int = 6) -> bool:
        """Tap close repeatedly until no modal/panel dim overlay is detected.

        Each attempt first checks the dark dim overlay (middleware
        _modal_is_open) — the generic "a modal is open" signal that works
        regardless of which panel it is or where its X sits. Close
        candidates, in order:

        1. dynamic red X (confirm modal, found at ~(1131, 56)),
        2. calibrated btn_close_universal ROI (research panel),
        3. a tap on the dimmed strip outside the panel ((30, 400) —
           the game dismisses modals on outside taps; only fired while
           a modal is confirmed open, so it can't hit home-screen UI).

        Returns True if the screen is clean (no dim overlay).
        """
        from backend.engine.middleware import _find_close_x_button, _modal_is_open

        for attempt in range(max_attempts):
            screen = await adb.screencap()
            if not screen:
                return False

            nparr = np.frombuffer(screen, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return False

            if not _modal_is_open(img):
                logger.info("Panel closed — no dim overlay detected")
                return True

            pos = _find_close_x_button(cv2.cvtColor(img, cv2.COLOR_BGR2HSV))
            if pos:
                logger.info("Closing panel: red X found at (%d,%d)", pos[0], pos[1])
                await human_tap(adb, pos[0], pos[1], sigma=3)
                await human_delay(0.5, 1.0)
                continue

            with get_session() as session:
                close_roi = session.query(RoiTemplate).filter_by(
                    roi_name="btn_close_universal").first()
            if close_roi:
                cx = close_roi.x_pos + close_roi.width // 2
                cy = close_roi.y_pos + close_roi.height // 2
                logger.info("Closing panel: btn_close_universal at (%d,%d)", cx, cy)
                await human_tap(adb, cx, cy, sigma=5)
                await human_delay(0.5, 1.0)
                continue

            logger.info("Closing panel: tapping dimmed strip at (30, 400)")
            await human_tap(adb, 30, 400, sigma=3)
            await human_delay(0.5, 1.0)

        logger.warning("Panel still open after %d close attempts", max_attempts)
        return False

    def _save_lab_confirm_debug(self, screen):
        """Write the lab confirm panel screenshot to storage/debug."""
        import time as _time
        _ts = _time.strftime("%Y%m%d_%H%M%S")
        with open(f"storage/debug/lab_confirm_{_ts}.png", "wb") as _f:
            _f.write(screen)
        logger.info("Debug: lab_confirm_%s.png saved", _ts)

    def _save_deploy_debug(self, screen):
        """Write the deploy screen screenshot to storage/debug (timestamped
        so captures never collide with each other)."""
        import time as _time, os as _os
        _ts = _time.strftime("%Y%m%d_%H%M%S")
        _os.makedirs("storage/debug", exist_ok=True)
        with open(f"storage/debug/deploy_full_{_ts}.png", "wb") as _f:
            _f.write(screen)
        logger.info("Debug: deploy_full_%s.png saved", _ts)

    async def _evaluate_mode(self, adb) -> str:
        """Determine whether to farm or upgrade. Returns 'farming' or 'upgrade'.

        Opens the builder menu and checks for a "Suggested Upgrade" header
        via template matching. No AI call — just image matching + OCR fallback.
        When builders are busy, also checks if a lab upgrade is available.
        """
        if self._lab_exhausted_until > time.time():
            logger.info("Lab rows exhausted — farming (retry after %s)",
                        time.strftime("%H:%M", time.localtime(self._lab_exhausted_until)))
            return "farming"

        await human_delay(0.5, 1.0)

        screen = await adb.screencap()
        if not screen:
            return "farming"

        builders = self._read_builder_count(screen)

        with get_session() as session:
            menu_roi = session.query(RoiTemplate).filter_by(roi_name="builder_menu").first()
        if not menu_roi:
            return "farming"
        menu_cx = menu_roi.x_pos + menu_roi.width // 2
        menu_cy = menu_roi.y_pos + menu_roi.height // 2

        from backend.vision.ocr import find_text

        if builders < 1:
            lab_status = self._read_lab_status(screen)
            logger.info("No free builders — lab status: %s", lab_status)
            if lab_status == "free":
                self._upgrade_target = {"type": "lab"}
                return "upgrade"
            return "farming"

        # Open builder menu for building upgrade check
        await human_tap(adb, menu_cx, menu_cy, sigma=3)
        await human_delay(1.5, 2.5)

        screen2 = await adb.screencap()
        if not screen2:
            await human_tap(adb, menu_cx, menu_cy, sigma=3)
            return "farming"

        # Check for suggested upgrade via OCR text detection
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

        if not self._upgrade_target:
            logger.info("No upgrade target — skipping")
            return

        # Laboratory upgrade — separate flow, no builders needed
        if self._upgrade_target and self._upgrade_target.get("type") == "lab":
            return await self._do_lab_upgrade(adb)

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
        tap_y = suggested_pos[1] + 38  # baris pertama di bawah header (terkalibrasi)
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
                    # Deploy failed — building may be in placement mode or cancelled.
                    # Tap empty area to dismiss any lingering dialogs/popups.
                    await human_tap(adb, 640, 360, sigma=50)
                    await human_delay(1.5, 2.5)
                    # Try to close Shop if still visible
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

        # Phase 3: Find Confirm button via template matching
        # Only scans bottom-right of the upgrade modal (x:600-800, y:580-700)
        # to avoid false positives on HP bars and grass terrain.
        screen = await adb.screencap()
        if not screen:
            self._upgrade_target = None
            return
        import numpy as _np, cv2 as _cv2, os as _os
        _nparr = _np.frombuffer(screen, _np.uint8)
        _cimg = _cv2.imdecode(_nparr, _cv2.IMREAD_COLOR)

        # DEBUG: Save screenshot before confirm detection
        self._confirm_debug_counter += 1
        _os.makedirs("storage/debug", exist_ok=True)
        _cv2.imwrite(f"storage/debug/confirm_debug_{self._confirm_debug_counter}.png", _cimg)
        logger.info("Debug: confirm_debug_%d.png saved", self._confirm_debug_counter)

        # Analyze confirm button status
        status, target_pos = analyze_confirm_button(_cimg)

        if status == "READY":
            click_x, click_y = target_pos
            logger.info(
                f"Resource cukup! Menekan tombol CONFIRM di ({click_x}, {click_y})"
            )
            await human_tap(adb, click_x, click_y, sigma=5)
            await human_delay(1.5, 2.5)
            logger.info("Upgrade berhasil dimulai!")

        elif status in ("INSUFFICIENT_RESOURCES", "TOWN_HALL_REQUIRED"):
            reason = "Resource kurang" if status == "INSUFFICIENT_RESOURCES" else "Town Hall required"
            logger.warning("%s! Membatalkan upgrade & menutup modal.", reason)
            # Tutup modal tanpa menekan tombol CONFIRM
            with get_session() as session:
                close_roi = session.query(RoiTemplate).filter_by(
                    roi_name="btn_close_universal").first()
            if close_roi:
                _cx = close_roi.x_pos + close_roi.width // 2
                _cy = close_roi.y_pos + close_roi.height // 2
                await human_tap(adb, _cx, _cy, sigma=5)
                await human_delay(1.0, 1.5)
            # Pindah ke Farming Mode untuk mencari resource
            self._loop_mode = "farming"
            self._upgrade_target = None
            return

        else:  # NOT_FOUND
            logger.warning(
                "Tombol CONFIRM tidak ditemukan di layar — menutup modal."
            )
            with get_session() as session:
                close_roi = session.query(RoiTemplate).filter_by(
                    roi_name="btn_close_universal").first()
            if close_roi:
                _cx = close_roi.x_pos + close_roi.width // 2
                _cy = close_roi.y_pos + close_roi.height // 2
                await human_tap(adb, _cx, _cy, sigma=5)
                await human_delay(1.0, 1.5)
            self._upgrade_target = None
            return

        logger.info("Upgrade confirmed — resources spent")
        self._upgrade_target = None

    async def _do_lab_upgrade(self, adb):
        """Execute laboratory research when builders are busy.

        Flow:
          1. Tap calibrated lab_upgrade ROI → opens research panel
          2. OCR "Suggested upgrades:" label + all research rows
          3. Try each row top-to-bottom: analyze_lab_confirm_button →
             confirm the first affordable one (red cost = skip row)
          4. Farming only when no row was affordable (resource kurang)
        """
        from backend.db.database import get_session
        from backend.db.models import RoiTemplate
        from backend.vision.ocr import find_text, find_texts

        logger.info("=== Laboratory Upgrade ===")

        # ── Pre-check: verify lab is still free before tapping ──
        screen = await adb.screencap()
        if screen:
            status = self._read_lab_status(screen)
            if status in ("busy", "unknown"):
                logger.info("Lab not available (%s) — skipping", status)
                self._upgrade_target = None
                return
            logger.info("Lab status pre-check: %s", status)

        # ── Phase 1: Tap lab icon → open research panel ──
        with get_session() as session:
            lab_roi = session.query(RoiTemplate).filter_by(
                roi_name="lab_upgrade").first()
        if not lab_roi:
            logger.warning("lab_upgrade ROI not calibrated")
            self._upgrade_target = None
            return

        lab_cx = lab_roi.x_pos + lab_roi.width // 2
        lab_cy = lab_roi.y_pos + lab_roi.height // 2
        logger.info("Phase 1: tapping lab_upgrade ROI at (%d,%d)", lab_cx, lab_cy)
        await human_tap(adb, lab_cx, lab_cy, sigma=3)
        await human_delay(1.5, 2.5)

        # ── Phase 2: Locate the Suggested section and its research rows ──
        screen = await adb.screencap()
        if not screen:
            self._upgrade_target = None
            return

        suggested_pos = find_text(screen, "Suggested upgrades") or find_text(screen, "Suggested")
        if not suggested_pos:
            logger.info("No 'Suggested upgrades:' in lab panel — closing panel")
            await self._close_panel_until_gone(adb)
            self._upgrade_target = None
            return

        # Rows live in the left column under the label; costs are right-
        # aligned (x>570) and filtered out by region. Positions come from
        # OCR so the flow adapts to any panel scroll state; section labels
        # ("... upgrades:") and pure digits are filtered out.
        label_x, label_y = suggested_pos
        rows = find_texts(screen, region=(250, label_y + 20, 570, 700))
        rows = [(t, cx, cy) for t, cx, cy, conf in rows
                if "upgrades" not in t.lower()
                and not t.replace(" ", "").isdigit()
                and conf >= 0.4]
        rows.sort(key=lambda r: r[2])
        logger.info("Phase 2: lab rows detected: %s", [r[0] for r in rows])

        if not rows:
            logger.info("No research rows detected — closing panel")
            await self._close_panel_until_gone(adb)
            self._upgrade_target = None
            return

        # ── Phase 3: Try each row top-to-bottom until one confirms ──
        started = False
        for text, row_cx, row_cy in rows[:5]:
            logger.info("Phase 3: trying row '%s' at (%d,%d)", text, label_x, row_cy)
            await human_tap(adb, label_x, row_cy, sigma=5)
            await human_delay(1.0, 2.0)

            screen = await adb.screencap()
            if not screen:
                break
            self._save_lab_confirm_debug(screen)

            _cimg = cv2.imdecode(np.frombuffer(screen, np.uint8), cv2.IMREAD_COLOR)
            status, target_pos = analyze_lab_confirm_button(_cimg)

            if status == "READY":
                logger.info("Phase 3: confirming '%s' at (%d,%d)",
                            text, target_pos[0], target_pos[1])
                await human_tap(adb, target_pos[0], target_pos[1], sigma=5)
                await human_delay(0.5, 1.0)

                # Verify the tap landed — the button disappears once the
                # research starts. Retap if it is still visible.
                for _ in range(2):
                    screen2 = await adb.screencap()
                    if not screen2:
                        break
                    _img2 = cv2.imdecode(np.frombuffer(screen2, np.uint8),
                                         cv2.IMREAD_COLOR)
                    st2, pos2 = analyze_lab_confirm_button(_img2)
                    if st2 != "READY":
                        started = True
                        break
                    logger.warning("Confirm button still visible — retapping")
                    await human_tap(adb, pos2[0], pos2[1], sigma=3)
                    await human_delay(0.5, 1.0)
                if started:
                    break
                # Button persisted after retaps — close and try next row
                await self._close_panel_until_gone(adb)
            elif status == "INSUFFICIENT_RESOURCES":
                logger.warning("Row '%s' tidak terjangkau — coba baris berikutnya", text)
                await self._close_panel_until_gone(adb)
                await human_delay(0.5, 1.0)
            else:
                # NOT_FOUND — no confirm modal opened. The row may be a
                # section-label fragment (e.g. "Other" from "Other upgrades:")
                # or a non-tappable element — skip and try the next row.
                logger.info("No confirm modal for row '%s' — skipping", text)
                await human_delay(0.5, 1.0)

        if started:
            logger.info("Lab research started!")
            self._lab_exhausted_until = 0
            await self._close_panel_until_gone(adb)
        else:
            logger.warning("Tidak ada research yang terjangkau — pindah farming")
            await self._close_panel_until_gone(adb)
            self._lab_exhausted_until = time.time() + 900
            self._loop_mode = "farming"

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

        # --- Phase A: Find and tap the orange arrow in Shop via HSV ---
        screen = await adb.screencap()
        if not screen:
            return False

        import numpy as _np, cv2 as _cv2
        _nparr = _np.frombuffer(screen, _np.uint8)
        _img = _cv2.imdecode(_nparr, _cv2.IMREAD_COLOR)

        from backend.engine.middleware import find_shop_arrow_cv

        arrow_pos = find_shop_arrow_cv(_img)
        if arrow_pos:
            logger.info("Arrow found via CV at (%d,%d)", arrow_pos[0], arrow_pos[1])
        else:
            logger.warning("Could not find Shop arrow via CV — purchase aborted")
            return False

        # Tap below arrow center — arrow points down at the building item
        await human_tap(adb, arrow_pos[0], arrow_pos[1] + 40, sigma=5)
        await human_delay(1.5, 2.5)

        # --- Phase B: Verify we left Shop (entered deploy screen) ---
        screen = await adb.screencap()
        if not screen:
            return False

        shop_still = match_template(
            screen, str(Path(self._TPL_DIR) / "icon_shop_tab.png"), threshold=0.6,
        )
        if shop_still:
            # Still in Shop — arrow tap missed, retry once with CV
            logger.warning("Still in Shop after tapping arrow — retrying with CV")
            import numpy as _np, cv2 as _cv2
            _nparr = _np.frombuffer(screen, _np.uint8)
            _retry_img = _cv2.imdecode(_nparr, _cv2.IMREAD_COLOR)
            retry_pos = find_shop_arrow_cv(_retry_img)
            if retry_pos:
                await human_tap(adb, retry_pos[0], retry_pos[1] + 40, sigma=5)
                await human_delay(1.5, 2.5)
                screen = await adb.screencap()
                if screen and match_template(
                    screen, str(Path(self._TPL_DIR) / "icon_shop_tab.png"),
                    threshold=0.6,
                ):
                    logger.warning("Still in Shop after retry — giving up")
                    return False
            else:
                logger.warning("CV could not find arrow on retry")
                return False

        logger.info("Deploy screen confirmed — building ready to place")
        self.current_screen = "home"

        # DEBUG: Save full deploy screen for analysis
        _dbg = await adb.screencap()
        if _dbg:
            self._save_deploy_debug(_dbg)

        # --- Phase C: Place the building ---
        return await self._place_building_after_purchase(adb)

    async def _place_building_after_purchase(self, adb) -> bool:
        """Tempatkan bangunan yang sudah dibeli.

        Bar X/centang MELAYANG di atas ghost dan mengikutinya (dinamis).
        Centang hijau = tempat valid → tap untuk konfirmasi. Abu-abu =
        terhalang → geser ghost ke kandidat grid berikutnya, cek ulang.
        Returns True HANYA kalau penempatan benar-benar terkonfirmasi
        (bar hilang setelah tap).
        """
        from backend.vision import deploy as deploy_vision

        # Grid kandidat di area desa (jauh dari HUD); urutan kiri-atas → kanan-bawah
        CANDIDATES = [
            (x, y)
            for y in range(220, 581, 120)
            for x in range(300, 1001, 140)
        ]

        placed = False
        last_bar = None
        prev_bar = None
        self._deploy_stuck_count = 0
        for attempt in range(len(CANDIDATES) + 1):
            screen = await adb.screencap()
            if not screen:
                break
            _img = cv2.imdecode(np.frombuffer(screen, np.uint8),
                                cv2.IMREAD_COLOR)
            if _img is None:
                break

            bar = deploy_vision.find_deploy_bar(_img)
            if not bar:
                logger.warning("Deploy bar tidak ditemukan (attempt %d) — "
                               "mode deploy tidak aktif", attempt)
                break

            last_bar = bar
            # Deteksi drag macet: bar tidak bergerak sejak attempt lalu
            if prev_bar is not None:
                if abs(bar[0] - prev_bar[0]) < 8 and abs(bar[1] - prev_bar[1]) < 8:
                    self._deploy_stuck_count += 1
                    logger.info("Bar tidak bergerak — stuck count=%d",
                                self._deploy_stuck_count)
                else:
                    self._deploy_stuck_count = 0
            prev_bar = bar

            state = deploy_vision.deploy_checkmark_state(_img, bar)
            ck_pos, x_pos = deploy_vision.deploy_button_centers(_img, bar)
            logger.info("Deploy attempt %d: bar=%s state=%s ck=%s",
                        attempt, bar, state, ck_pos)
            import os as _os
            _os.makedirs("storage/debug", exist_ok=True)
            cv2.imwrite(f"storage/debug/deploy_attempt_{attempt:02d}.png", _img)

            if state == "green":
                # Tempat valid — tap centang hijau, lalu VERIFIKASI bar
                # hilang SECARA LOKAL (150px sekitar titik tap). Bar palsu
                # dari bangunan gelap desa di tempat lain tidak boleh
                # memicu retap.
                await human_tap(adb, ck_pos[0], ck_pos[1], sigma=3)
                await human_delay(1.5, 2.0)
                for retry in range(2):
                    v = await adb.screencap()
                    if not v:
                        break
                    _v = cv2.imdecode(np.frombuffer(v, np.uint8),
                                      cv2.IMREAD_COLOR)
                    if _v is not None:
                        vbar = deploy_vision.find_deploy_bar(_v)
                        near = vbar and (
                            abs((vbar[0] + vbar[2] // 2) - ck_pos[0]) < 150
                            and abs((vbar[1] + vbar[3] // 2) - ck_pos[1]) < 150
                        )
                        if not near:
                            placed = True
                            break
                    logger.warning("Deploy bar masih ada — retap centang")
                    await human_tap(adb, ck_pos[0], ck_pos[1], sigma=2)
                    await human_delay(1.0, 1.5)
                break

            # Terhalang — geser ghost ke kandidat berikutnya.
            # Drag harus MULAI di ghost (di bawah bar), kalau tidak malah
            # pan peta dan bangunan diam.
            if attempt >= len(CANDIDATES):
                break
            tx, ty = CANDIDATES[attempt]
            bx, by, bw, bh = bar
            ghost_x = bx + bw // 2
            # Pusat ghost ≈ 140px di bawah TEPI ATAS bar (terukur dari
            # fixture valid & blocked). Tinggi bar hasil deteksi sering
            # parsial — offset dari tepi atas jauh lebih stabil.
            ghost_y = by + 140
            # Kalau drag sebelumnya tidak menggerakkan bar (kena bangunan/
            # HUD), geser titik start supaya tidak kena titik mati yang sama.
            if getattr(self, "_deploy_stuck_count", 0) > 0:
                ghost_x += (self._deploy_stuck_count % 2) * 30 - 15
            logger.info("Terhalang — geser ghost (%d,%d) ke (%d,%d)",
                        ghost_x, ghost_y, tx, ty)
            await adb.swipe(ghost_x, ghost_y, tx, ty, duration_ms=250)
            await human_delay(0.6, 1.0)

        if not placed:
            # Batalkan penempatan lewat tombol X di bar (posisi terakhir
            # terdeteksi). JANGAN bohong sukses — return False.
            logger.warning("Gagal menempatkan bangunan setelah %d kandidat — "
                           "membatalkan", len(CANDIDATES))
            if last_bar:
                cancel_screen = await adb.screencap()
                if cancel_screen:
                    _cimg = cv2.imdecode(np.frombuffer(cancel_screen, np.uint8),
                                         cv2.IMREAD_COLOR)
                    if _cimg is not None:
                        _, x_pos = deploy_vision.deploy_button_centers(
                            _cimg, last_bar)
                        logger.info("Membatalkan — tap X di (%d,%d)",
                                    x_pos[0], x_pos[1])
                        await human_tap(adb, x_pos[0], x_pos[1], sigma=3)
                        await human_delay(1.0, 1.5)
            return False

        logger.info("New building purchased and deployed successfully")
        self._upgrade_target = None
        return True

    async def _poll_countdown_then_return(self, adb):
        """Poll countdown timer; when it disappears, battle is over.

        After battle ends: checks for Claim Reward / Return Home buttons first,
        then exits. Guards against Shop/menu accidentally opening mid-attack.
        """
        from backend.vision.matching import match_template
        from backend.engine.middleware import resolve_blockers, _find_return_home_button, _find_claim_reward_button
        import numpy as _np, cv2 as _cv2
        import time as _time

        TPL_COUNTDOWN = f"{self._TPL_DIR}/btn_countdown.png"

        logger.info("Watching countdown timer...")
        _start = _time.monotonic()
        _max_watch = 240  # max 4 minutes — safety timeout
        while self._running:
            if _time.monotonic() - _start > _max_watch:
                logger.warning("Countdown watch timeout (%ds) — forcing exit", _max_watch)
                return

            await asyncio.sleep(3)
            screen = await adb.screencap()
            if not screen:
                continue

            _img = _cv2.imdecode(_np.frombuffer(screen, _np.uint8), _cv2.IMREAD_COLOR)
            hsv = _cv2.cvtColor(_img, _cv2.COLOR_BGR2HSV)
            countdown_found = match_template(screen, TPL_COUNTDOWN, threshold=0.40)

            # Check for troop bar (attack screen)
            troop_bar_roi = _img[_img.shape[0] - 120:_img.shape[0], 0:_img.shape[1]]
            troop_hsv = _cv2.cvtColor(troop_bar_roi, _cv2.COLOR_BGR2HSV)
            grey_mask = _cv2.inRange(
                troop_hsv, _np.array([0, 0, 40]), _np.array([180, 30, 180]),
            )
            has_troop_bar = _np.count_nonzero(grey_mask) / grey_mask.size > 0.15

            if countdown_found or has_troop_bar:
                continue  # battle still in progress

            # Both countdown and troop bar gone — battle likely ended.
            end_pos = _find_claim_reward_button(hsv) or _find_return_home_button(hsv)
            if end_pos:
                logger.info("Battle finished — tapping end screen at (%d,%d)", end_pos[0], end_pos[1])
                await human_tap(adb, end_pos[0], end_pos[1], sigma=5)
                await human_delay(2.5, 3.5)
                return

            # Fallback: HSV detection failed but battle is definitely over.
            logger.warning("Battle finished but HSV missed button — tapping fallback (500, 660)")
            await human_tap(adb, 500, 660, sigma=10)
            await human_delay(2.5, 3.5)
            return

    async def _do_return_home(self, adb):
        """Return home after battle, dismissing event popups on the way.

        Up to 6 attempts. Each attempt: confirm home first (no blind tap
        when already there) → resolve popup blockers (Return Home / Claim /
        Continue / event animation / close X) → fallback tap on the
        return-home ROI. Raises ScreenVerificationError if home is never
        reached instead of silently pretending it succeeded.
        """
        self.state = "RETURNING"
        logger.info("Returning home...")

        from backend.engine.middleware import resolve_blockers

        with get_session() as session:
            from backend.db.models import RoiTemplate
            home_roi = session.query(RoiTemplate).filter_by(roi_name="btn_return_home").first()
        if home_roi:
            cx = home_roi.x_pos + home_roi.width // 2
            cy = home_roi.y_pos + home_roi.height // 2
        else:
            cx, cy = 640, 650

        for attempt in range(6):
            screen = await adb.screencap()
            if screen and await self._is_home_screen(adb, screen=screen):
                logger.info("Home screen confirmed (attempt %d/6)", attempt + 1)
                break

            # Popup blocker takes priority over a blind return-home tap —
            # Continue / event-animation / close-X popups stack after battles.
            if screen:
                _img = cv2.imdecode(np.frombuffer(screen, np.uint8),
                                    cv2.IMREAD_COLOR)
                blocked, pos = resolve_blockers(_img)
                if blocked and pos:
                    logger.info("Return home %d/6: blocker at (%d,%d)",
                                attempt + 1, pos[0], pos[1])
                    await human_tap(adb, pos[0], pos[1], sigma=5)
                    await human_delay(1.5, 2.5)
                    continue

            logger.info("Return home %d/6: tapping return-home at (%d,%d)",
                        attempt + 1, cx, cy)
            await human_tap(adb, cx, cy, sigma=15)
            await human_delay(3.0, 5.0)
        else:
            logger.error("Return home failed after 6 attempts")
            self.state = "RUNNING"
            raise ScreenVerificationError("Could not reach home after 6 attempts")

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
