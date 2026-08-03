"""Finite State Machine controller for the CoC bot."""

import asyncio
import logging
import random
import time
from enum import Enum, auto

from backend.adb.manager import adb_manager
from backend.config import settings

logger = logging.getLogger(__name__)


class BotState(Enum):
    INIT = auto()
    MAIN_BASE = auto()
    TRAINING = auto()
    SEARCHING = auto()
    ATTACKING = auto()
    RETURN_HOME = auto()
    UPGRADING = auto()
    RECOVERY = auto()
    DEAD = auto()
    STOPPED = auto()


STATE_TIMEOUTS = {
    BotState.INIT: 30,
    BotState.SEARCHING: 300,
    BotState.ATTACKING: 240,
    BotState.TRAINING: 2400,
    BotState.UPGRADING: 3600,
}


class FsmController:
    """Controls the bot's state machine lifecycle."""

    def __init__(self, adb=None):
        self.adb = adb or adb_manager
        self.state: BotState = BotState.STOPPED
        self._running = False
        self._task: asyncio.Task | None = None
        self.state_started_at: float = 0
        self.recovery_count: int = 0

        # Break interval scheduler
        self.break_interval: int = 7200  # 2 hours
        self.break_duration_min: int = 600  # 10 min
        self.break_duration_max: int = 1200  # 20 min
        self._last_break_time: float = 0

        # Runtime stats
        self.gold_earned: int = 0
        self.elixir_earned: int = 0
        self.dark_elixir_earned: int = 0
        self.raids_completed: int = 0

    def transition(self, new_state: BotState):
        old = self.state
        self.state = new_state
        self.state_started_at = time.time()
        logger.info("FSM: %s -> %s", old.name, new_state.name)

        if new_state != BotState.RECOVERY:
            self.recovery_count = 0

    @property
    def is_running(self) -> bool:
        return self._running

    def get_status_dict(self) -> dict:
        return {
            "state": self.state.name,
            "running": self._running,
            "gold_earned": self.gold_earned,
            "elixir_earned": self.elixir_earned,
            "dark_elixir_earned": self.dark_elixir_earned,
            "raids_completed": self.raids_completed,
        }

    async def maybe_take_break(self):
        """Check if it's time for a rest break. Returns True if break was taken."""
        if self._last_break_time == 0:
            self._last_break_time = time.time()
            return False

        elapsed = time.time() - self._last_break_time
        if elapsed < self.break_interval:
            return False

        import random
        duration = random.randint(self.break_duration_min, self.break_duration_max)
        logger.info("FSM: Taking break for %d seconds (%d minutes)", duration, duration // 60)
        await asyncio.sleep(duration)
        self._last_break_time = time.time()
        logger.info("FSM: Break finished, resuming")
        return True

    def check_timeout(self) -> bool:
        """Return True if the current state has timed out."""
        timeout = STATE_TIMEOUTS.get(self.state)
        if timeout is None:
            return False
        elapsed = time.time() - self.state_started_at
        return elapsed > timeout

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("FSM started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.transition(BotState.STOPPED)
        logger.info("FSM stopped")

    async def _run_loop(self):
        """Main FSM loop. Each iteration handles one state."""
        self.transition(BotState.INIT)

        while self._running:
            if self.state in (BotState.STOPPED, BotState.DEAD):
                break

            # Check for break interval
            await self.maybe_take_break()

            # Check for timeout
            if self.check_timeout() and self.state != BotState.INIT:
                logger.warning(
                    "FSM: %s timed out, entering recovery", self.state.name
                )
                await self._do_recovery()
                continue

            try:
                await self._handle_state()
            except Exception as e:
                logger.error(
                    "FSM error in state %s: %s", self.state.name, e, exc_info=True
                )
                self.recovery_count += 1
                if self.recovery_count > 3:
                    logger.critical(
                        "FSM: too many recoveries, entering DEAD state"
                    )
                    self.transition(BotState.DEAD)
                    break
                await self._do_recovery()

            await asyncio.sleep(0.5)

    async def _handle_state(self):
        """Dispatch to the appropriate state handler."""
        if self.state == BotState.INIT:
            await self._state_init()
        elif self.state == BotState.MAIN_BASE:
            await self._state_main_base()
        elif self.state == BotState.TRAINING:
            await self._state_training()
        elif self.state == BotState.SEARCHING:
            await self._state_searching()
        elif self.state == BotState.ATTACKING:
            await self._state_attacking()
        elif self.state == BotState.RETURN_HOME:
            await self._state_return_home()
        elif self.state == BotState.UPGRADING:
            await self._state_upgrading()
        elif self.state == BotState.DEAD:
            logger.critical(
                "FSM: DEAD state reached -- manual restart required"
            )

    async def _state_init(self):
        """Initialize: verify ADB connection, ensure CoC is open."""
        if not self.adb.is_connected:
            logger.info("FSM: connecting ADB...")
            connected = await self.adb.connect()
            if not connected:
                logger.error("FSM: ADB connection failed")
                self.recovery_count += 1
                return
            await self.adb.set_resolution()

        logger.info("FSM: INIT complete")
        self.transition(BotState.MAIN_BASE)

    async def _state_main_base(self):
        """Main base: dismiss popups, check army readiness."""
        from backend.humanize import human_tap, human_delay

        logger.info("FSM: MAIN_BASE -- checking village")

        # Dismiss any popups/trader offers (tap center of screen)
        await human_tap(self.adb, 640, 400, sigma=80)
        await human_delay(0.5, 1.0)

        # Go straight to attack sequence — skip collector tapping
        self.transition(BotState.SEARCHING)

    async def _state_training(self):
        """Train troops: open barracks, tap quick train, wait."""
        from backend.humanize import human_tap, human_delay

        logger.info("FSM: TRAINING -- opening barracks")

        # Tap Army overview button (approximate)
        await human_tap(self.adb, 70, 530, sigma=5)
        await human_delay(1.0, 2.0)

        # Tap Quick Train button (approximate)
        await human_tap(self.adb, 640, 620, sigma=10)
        await human_delay(0.5, 1.0)

        # Close barracks overlay
        await human_tap(self.adb, 640, 400, sigma=50)
        await human_delay(0.3, 0.5)

        # Wait for training timer (simulated -- real implementation would read timer via OCR)
        logger.info("FSM: TRAINING -- waiting for troops (simulated 15s)")
        await asyncio.sleep(15)

        self.transition(BotState.MAIN_BASE)

    async def _state_searching(self):
        """Search for a base with sufficient loot. Tap Next until threshold met."""
        from backend.humanize import human_tap, human_delay
        from backend.vision.ocr import read_number
        from backend.db.database import get_session
        from backend.db.models import RoiTemplate, Config

        logger.info("FSM: SEARCHING -- tapping buttons via calibrated ROIs")

        # Tap Attack button - use calibrated ROI center
        attack_roi = session.query(RoiTemplate).filter_by(roi_name="btn_attack").first() if 'session' in dir() else None

        with get_session() as session:
            attack_roi = session.query(RoiTemplate).filter_by(roi_name="btn_attack").first()
            find_roi = session.query(RoiTemplate).filter_by(roi_name="btn_find_match").first()
            gold_roi = session.query(RoiTemplate).filter_by(roi_name="gold_number").first()
            elixir_roi = session.query(RoiTemplate).filter_by(roi_name="elixir_number").first()
            next_roi = session.query(RoiTemplate).filter_by(roi_name="btn_next").first()

            min_gold_th = session.query(Config).filter_by(key="min_gold_threshold").first()
            min_elixir_th = session.query(Config).filter_by(key="min_elixir_threshold").first()
            min_gold = int(min_gold_th.value) if min_gold_th else 300000
            min_elixir = int(min_elixir_th.value) if min_elixir_th else 300000

        # Tap Attack button using calibrated ROI center
        if attack_roi:
            ax = attack_roi.x_pos + attack_roi.width // 2
            ay = attack_roi.y_pos + attack_roi.height // 2
            await human_tap(self.adb, ax, ay, sigma=5)
        else:
            await human_tap(self.adb, 70, 580, sigma=5)
        await human_delay(2.0, 3.0)

        # Tap Find Match using calibrated ROI center
        if find_roi:
            fx = find_roi.x_pos + find_roi.width // 2
            fy = find_roi.y_pos + find_roi.height // 2
            await human_tap(self.adb, fx, fy, sigma=10)
        else:
            await human_tap(self.adb, 640, 600, sigma=10)
        await human_delay(5.0, 8.0)  # Wait for match search

        # Tap "Attack" button on army confirmation screen
        with get_session() as session:
            army_atk_roi = session.query(RoiTemplate).filter_by(roi_name="myarmy_btn_attack").first()
        if army_atk_roi:
            mx = army_atk_roi.x_pos + army_atk_roi.width // 2
            my = army_atk_roi.y_pos + army_atk_roi.height // 2
            await human_tap(self.adb, mx, my, sigma=5)
        await human_delay(2.0, 4.0)  # Wait for match to be found

        # Search loop: read loot, Next if below threshold
        search_count = 0
        max_searches = 30

        while search_count < max_searches and self._running:
            search_count += 1
            await human_delay(1.0, 2.0)

            screen = await self.adb.screencap()
            if not screen:
                await human_delay(0.5, 1.0)
                continue

            gold_val = None
            elixir_val = None

            if gold_roi:
                gold_val = read_number(screen, gold_roi.x_pos, gold_roi.y_pos, gold_roi.width, gold_roi.height)
            if elixir_roi:
                elixir_val = read_number(screen, elixir_roi.x_pos, elixir_roi.y_pos, elixir_roi.width, elixir_roi.height)

            logger.info("FSM: Search #%d -- Gold: %s, Elixir: %s", search_count, gold_val, elixir_val)

            if gold_val and gold_val >= min_gold and elixir_val and elixir_val >= min_elixir:
                logger.info("FSM: Target found! Gold=%d Elixir=%d", gold_val, elixir_val)
                self._search_count = search_count
                self.transition(BotState.ATTACKING)
                return

            # Tap Next
            if next_roi:
                await human_tap(self.adb, next_roi.x_pos + next_roi.width // 2, next_roi.y_pos + next_roi.height // 2, sigma=5)
            else:
                await human_tap(self.adb, 640, 660, sigma=15)

            # Occasional random skip (5-10%)
            if random.random() < 0.08 and gold_val and gold_val >= min_gold * 0.7:
                logger.info("FSM: Random skip on acceptable base")
                _search_count = search_count
                self.transition(BotState.ATTACKING)
                return

            await human_delay(1.2, 3.5)

        # Max searches reached, attack whatever we have or return home
        logger.info("FSM: Max searches reached")
        self._search_count = search_count
        self.transition(BotState.ATTACKING)

    async def _state_attacking(self):
        """Deploy troops and wait for battle to end."""
        from backend.humanize import human_swipe, human_delay, human_tap

        logger.info("FSM: ATTACKING -- deploying troops")

        # Tap to start attack (if on preview screen)
        await human_tap(self.adb, 640, 650, sigma=10)
        await human_delay(1.0, 2.0)

        # Deploy troops: 4-finger drop along the top perimeter
        # Tap troop icon (first troop slot)
        await human_tap(self.adb, 160, 680, sigma=5)
        await human_delay(0.3, 0.6)

        # Swipe deploy across the top
        deployment_zones = [
            (100, 50, 400, 200),
            (400, 50, 700, 200),
            (700, 50, 1000, 200),
            (100, 200, 400, 350),
            (400, 200, 700, 350),
        ]

        for sx, sy, ex, ey in deployment_zones:
            await human_swipe(self.adb, sx + random.randint(-20, 20), sy, ex + random.randint(-20, 20), ey, duration_ms=random.randint(200, 400))
            await human_delay(0.5, 1.0)

        # Wait for battle to complete (typical attack duration)
        logger.info("FSM: ATTACKING -- waiting for battle")
        await asyncio.sleep(180)  # 3 minutes

        self.transition(BotState.RETURN_HOME)

    async def _state_return_home(self):
        """Return to village after battle, log results."""
        from backend.humanize import human_tap, human_delay
        from backend.vision.ocr import read_number
        from backend.db.database import get_session
        from backend.db.models import RoiTemplate, AttackLog

        logger.info("FSM: RETURN_HOME")

        # Wait for post-battle screen
        await human_delay(5.0, 10.0)

        # Tap "Return Home" button
        await human_tap(self.adb, 640, 650, sigma=15)
        await human_delay(3.0, 5.0)

        # Log attack results (placeholder -- would read from post-battle screen)
        gold = self.gold_earned
        elixir = self.elixir_earned
        de = self.dark_elixir_earned
        trophies = 0
        search_count = getattr(self, "_search_count", 1)

        try:
            with get_session() as session:
                log = AttackLog(
                    gold_earned=gold,
                    elixir_earned=elixir,
                    dark_elixir_earned=de,
                    trophies_change=trophies,
                    search_count=search_count,
                )
                session.add(log)
                session.commit()
                logger.info("FSM: Attack logged -- G:%d E:%d DE:%d searches:%d", gold, elixir, de, search_count)
        except Exception as e:
            logger.error("FSM: Failed to log attack: %s", e)

        self.raids_completed += 1
        self.transition(BotState.MAIN_BASE)

    async def _state_upgrading(self):
        """Start a building upgrade from the priority queue."""
        from backend.humanize import human_tap, human_delay

        logger.info("FSM: UPGRADING -- checking build queue")
        # TODO: Full upgrade logic in Phase 5
        await human_delay(1.0, 2.0)
        self.transition(BotState.MAIN_BASE)

    async def _do_recovery(self):
        """Recovery procedure: restart game and return to known state."""
        logger.info("FSM: RECOVERY -- restarting game...")
        self.transition(BotState.RECOVERY)

        if self.adb.is_connected:
            # Force stop CoC
            try:
                await self.adb._run_adb(
                    "-s", self.adb._serial, "shell",
                    "am", "force-stop", "com.supercell.clashofclans",
                )
            except Exception:
                pass
            # Relaunch
            try:
                await self.adb._run_adb(
                    "-s", self.adb._serial, "shell",
                    "monkey", "-p", "com.supercell.clashofclans",
                        "-c", "android.intent.category.LAUNCHER", "1",
                )
            except Exception:
                pass

        await asyncio.sleep(5)  # Wait for game to load
        self.transition(BotState.INIT)


fsm_controller = FsmController()
