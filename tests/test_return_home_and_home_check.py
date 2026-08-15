"""Tests for robust return-home, dual-signal home check, and the farming
loop home-screen reset (StillAtHomeError → upgrade loop)."""

import cv2
import numpy as np
import pytest

import backend.engine.sequence_runner as seq_mod
from backend.engine.sequence_runner import (
    ScreenVerificationError,
    SequenceRunner,
    StillAtHomeError,
)


def _png_bytes():
    img = np.zeros((720, 1280, 3), dtype=np.uint8)
    _, buf = cv2.imencode(".png", img)
    return buf.tobytes()


class _FakeAdb:
    def __init__(self, screens=None):
        self.screens = list(screens or [])

    async def screencap(self):
        if not self.screens:
            return None
        return self.screens.pop(0)


class _FakeRoi:
    def __init__(self, name="btn_return_home", x=50, y=580, w=160, h=100):
        self.roi_name = name
        self.x_pos = x
        self.y_pos = y
        self.width = w
        self.height = h
        self.value = "300000"  # Config lookups (min thresholds)


class _FakeSession:
    """Fake DB session: RoiTemplate queries → _FakeRoi, Config → value,
    AttackLog add/commit → no-op."""

    def __init__(self):
        self.added = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def query(self, model):
        return self

    def filter_by(self, **kwargs):
        return self

    def first(self):
        return _FakeRoi()

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass


@pytest.fixture
def runner():
    return SequenceRunner()


# ── _is_home_screen dual signal + no-modal gate ──────────────────────


def _patch_home_signals(monkeypatch, attack=(100, 600), shop=(1216, 660),
                        modal_open=False):
    monkeypatch.setattr("backend.vision.ocr.find_text",
                        lambda cap, kw: attack)
    monkeypatch.setattr(
        "backend.vision.matching.match_template",
        lambda cap, path, threshold=0.6: shop,
    )
    monkeypatch.setattr(
        "backend.engine.middleware._modal_is_open",
        lambda img: modal_open,
    )


@pytest.mark.asyncio
async def test_home_screen_requires_both_attack_and_shop(monkeypatch, runner):
    _patch_home_signals(monkeypatch)
    assert await runner._is_home_screen(_FakeAdb(), screen=_png_bytes()) is True


@pytest.mark.asyncio
async def test_home_screen_false_without_shop_button(monkeypatch, runner):
    _patch_home_signals(monkeypatch, shop=None)
    assert await runner._is_home_screen(_FakeAdb(), screen=_png_bytes()) is False


@pytest.mark.asyncio
async def test_home_screen_false_without_attack_button(monkeypatch, runner):
    _patch_home_signals(monkeypatch, attack=None)
    assert await runner._is_home_screen(_FakeAdb(), screen=_png_bytes()) is False


@pytest.mark.asyncio
async def test_home_screen_rejects_shop_match_outside_bottom_right(monkeypatch, runner):
    # "Attack" ok but the shop match is top-right — not the home button
    _patch_home_signals(monkeypatch, shop=(1216, 400))
    assert await runner._is_home_screen(_FakeAdb(), screen=_png_bytes()) is False


@pytest.mark.asyncio
async def test_home_screen_false_when_modal_is_open(monkeypatch, runner):
    # both buttons visible but a popup dims the screen — NOT home
    _patch_home_signals(monkeypatch, modal_open=True)
    assert await runner._is_home_screen(_FakeAdb(), screen=_png_bytes()) is False


def test_home_screen_rejects_real_star_bonus_popup(runner):
    """Real capture: Star Bonus popup over home with Attack!+Shop visible.
    The no-modal gate must reject it (the old dual signal accepted it)."""
    import asyncio
    from pathlib import Path

    cap = Path(__file__).parent / "fixtures" / "star_bonus_popup.png"

    async def _run():
        return await runner._is_home_screen(_FakeAdb(), screen=cap.read_bytes())

    assert asyncio.run(_run()) is False


def test_home_screen_accepts_real_clean_home(runner):
    """Real capture: clean home screen must still pass."""
    import asyncio
    from pathlib import Path

    cap = Path(__file__).parent / "fixtures" / "home_day_clean.png"

    async def _run():
        return await runner._is_home_screen(_FakeAdb(), screen=cap.read_bytes())

    assert asyncio.run(_run()) is True


# ── _do_return_home with popup blockers ──────────────────────────────


def _patch_return_home_common(monkeypatch, runner, tap_log):
    async def fake_tap(adb, x, y, sigma=0):
        tap_log.append((x, y))

    async def fake_delay(*args, **kwargs):
        pass

    monkeypatch.setattr(seq_mod, "human_tap", fake_tap)
    monkeypatch.setattr(seq_mod, "human_delay", fake_delay)
    monkeypatch.setattr("backend.db.database.get_session", lambda: _FakeSession())


@pytest.mark.asyncio
async def test_return_home_already_home_taps_nothing(monkeypatch, runner):
    tap_log = []
    _patch_return_home_common(monkeypatch, runner, tap_log)
    monkeypatch.setattr(
        runner, "_is_home_screen", _fake_home([True])
    )

    await runner._do_return_home(_FakeAdb(screens=[_png_bytes()]))

    assert tap_log == []
    assert runner.raids_completed == 1
    assert runner.current_screen == "home"


def _fake_home(results):
    async def fake(adb, screen=None):
        return results.pop(0) if results else False

    return fake


@pytest.mark.asyncio
async def test_return_home_dismisses_continue_popup_first(monkeypatch, runner):
    tap_log = []
    _patch_return_home_common(monkeypatch, runner, tap_log)
    monkeypatch.setattr(runner, "_is_home_screen", _fake_home([False, True]))
    monkeypatch.setattr(
        "backend.engine.middleware.resolve_blockers",
        lambda img: (True, (500, 650)),
    )

    await runner._do_return_home(_FakeAdb(screens=[_png_bytes(), _png_bytes()]))

    # the Continue popup button was tapped, not the return-home ROI
    assert tap_log == [(500, 650)]
    assert runner.current_screen == "home"


@pytest.mark.asyncio
async def test_return_home_raises_when_home_never_reached(monkeypatch, runner):
    tap_log = []
    _patch_return_home_common(monkeypatch, runner, tap_log)
    monkeypatch.setattr(runner, "_is_home_screen", _fake_home([False] * 7))
    monkeypatch.setattr(
        "backend.engine.middleware.resolve_blockers",
        lambda img: (False, None),
    )

    screens = [_png_bytes() for _ in range(7)]
    with pytest.raises(ScreenVerificationError):
        await runner._do_return_home(_FakeAdb(screens=screens))

    # 6 attempts — return-home ROI tapped each time, no blockers
    assert len(tap_log) == 6


# ── _do_search home guard ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_do_search_raises_still_at_home(monkeypatch, runner):
    runner._running = True
    monkeypatch.setattr(seq_mod, "read_number", lambda *a, **k: 1000)
    monkeypatch.setattr("backend.db.database.get_session", lambda: _FakeSession())
    monkeypatch.setattr(runner, "_verify_search_screen", _noop)
    monkeypatch.setattr(runner, "_is_home_screen", _fake_home([True]))

    async def fake_tap(adb, x, y, sigma=0):
        pass

    async def fake_delay(*args, **kwargs):
        pass

    monkeypatch.setattr(seq_mod, "human_tap", fake_tap)
    monkeypatch.setattr(seq_mod, "human_delay", fake_delay)

    class _Step:
        config_json = '{"max_searches": 3}'

    with pytest.raises(StillAtHomeError):
        await runner._do_search(_Step(), _FakeAdb(screens=[_png_bytes()]))


async def _noop(*args, **kwargs):
    return None


# ── _run resets to upgrade loop when still at home ───────────────────


class _FakeSeq:
    def __init__(self, name, id_):
        self.name = name
        self.id = id_


class _FakeStep:
    def __init__(self, step_type, roi_name=None):
        self.step_type = step_type
        self.roi_name = roi_name


class _FakeExecResult:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value[0] if self._value else None

    def all(self):
        return self._value


class _FakeRunSession:
    """Serves _run's four queries in their fixed call order:
    1. Farming Loop sequence, 2. Upgrade Loop sequence,
    3. farming steps, 4. upgrade steps."""

    def __init__(self, farming_steps, upgrade_steps):
        self._farming = farming_steps
        self._upgrade = upgrade_steps
        self._calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def exec(self, stmt):
        self._calls += 1
        n = self._calls
        if n == 1:
            return _FakeExecResult([_FakeSeq("Farming Loop", 1)])
        if n == 2:
            return _FakeExecResult([_FakeSeq("Upgrade Loop", 2)])
        if n == 3:
            return _FakeExecResult(self._farming)
        return _FakeExecResult(self._upgrade)


class _FakeAdbManager:
    is_connected = True

    async def connect(self):
        return True


@pytest.mark.asyncio
async def test_run_resets_to_upgrade_loop_when_still_at_home(monkeypatch, runner):
    runner._running = True
    farming = [_FakeStep("tap", "btn_attack"), _FakeStep("search"), _FakeStep("attack")]
    upgrade = [_FakeStep("upgrade_check"), _FakeStep("upgrade_execute")]
    executed = []

    # _run uses the module-level import — patch seq_mod.get_session
    monkeypatch.setattr(seq_mod, "get_session",
                        lambda: _FakeRunSession(farming, upgrade))
    monkeypatch.setattr(seq_mod, "adb_manager", _FakeAdbManager())

    async def fake_verify(adb, step_type, roi_name=None):
        return True

    async def fake_execute(step, adb):
        executed.append((step.step_type, step.roi_name))

    async def fake_home(adb, screen=None):
        return True

    evaluated_with = []

    async def fake_evaluate(adb):
        evaluated_with.append(runner._loop_mode)
        runner._running = False
        return "farming"

    async def fake_resources():
        pass

    async def fake_delay(*args, **kwargs):
        pass

    monkeypatch.setattr(runner, "_verify_step_screen", fake_verify)
    monkeypatch.setattr(runner, "_execute_step", fake_execute)
    monkeypatch.setattr(runner, "_is_home_screen", fake_home)
    monkeypatch.setattr(runner, "_evaluate_mode", fake_evaluate)
    monkeypatch.setattr(runner, "read_current_resources", fake_resources)
    monkeypatch.setattr(seq_mod, "human_delay", fake_delay)

    await runner._run(sequence_id=None)

    # attack never ran — the home guard fired on the search step
    assert executed == [("tap", "btn_attack")]
    # the loop was reset to upgrade mode before re-evaluating
    assert evaluated_with == ["upgrade"]
    assert runner._loop_mode == "farming"  # evaluate result applied after reset
