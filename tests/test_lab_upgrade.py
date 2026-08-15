"""Tests for _do_lab_upgrade confirm handling and row iteration."""

import cv2
import numpy as np
import pytest

import backend.engine.sequence_runner as seq_mod
from backend.engine.sequence_runner import SequenceRunner


class _FakeRoi:
    x_pos = 408
    y_pos = 23
    width = 137
    height = 40


class _FakeSession:
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


class _FakeAdb:
    async def screencap(self):
        img = np.zeros((720, 1280, 3), dtype=np.uint8)
        _, buf = cv2.imencode(".png", img)
        return buf.tobytes()


_ROWS = [("New Wall Breaker", 400, 163, 0.9), ("Archer", 400, 239, 0.9)]


def _patch_lab_common(monkeypatch, runner, tap_log, analyze_results):
    """Wire the common mocks for _do_lab_upgrade tests."""
    monkeypatch.setattr(runner, "_save_lab_confirm_debug", lambda screen: None)
    monkeypatch.setattr(runner, "_read_lab_status", lambda screen: "free")

    close_calls = []

    async def fake_tap(adb, x, y, sigma=0):
        tap_log.append((x, y))

    async def fake_delay(*args, **kwargs):
        pass

    async def fake_close_panel(adb, max_attempts=6):
        close_calls.append(1)

    results = iter(analyze_results)

    monkeypatch.setattr(runner, "_close_panel_until_gone", fake_close_panel)
    monkeypatch.setattr(seq_mod, "human_tap", fake_tap)
    monkeypatch.setattr(seq_mod, "human_delay", fake_delay)
    monkeypatch.setattr("backend.db.database.get_session", lambda: _FakeSession())
    monkeypatch.setattr(
        "backend.vision.ocr.find_text", lambda screen, keyword: (500, 125)
    )
    monkeypatch.setattr(
        "backend.vision.ocr.find_texts",
        lambda screen, region=None, min_conf=0.3: _ROWS,
    )
    monkeypatch.setattr(
        seq_mod, "analyze_upgrade_confirm_button", lambda img: next(results)
    )
    return close_calls


@pytest.mark.asyncio
async def test_all_rows_insufficient_then_farming(monkeypatch):
    runner = SequenceRunner()
    runner._upgrade_target = {"type": "lab"}
    runner._loop_mode = "upgrade"
    tap_log = []
    # row1 INSUFFICIENT, row2 INSUFFICIENT → exhausted → farming
    analyze_results = [
        ("INSUFFICIENT_RESOURCES", None),
        ("INSUFFICIENT_RESOURCES", None),
    ]
    close_calls = _patch_lab_common(monkeypatch, runner, tap_log, analyze_results)

    await runner._do_lab_upgrade(_FakeAdb())

    # both rows tapped
    assert (500, 163) in tap_log
    assert (500, 239) in tap_log
    # modal closed per row + list closed at the end
    assert len(close_calls) >= 3
    assert runner._loop_mode == "farming"
    assert runner._lab_exhausted_until > 0
    assert runner._upgrade_target is None


@pytest.mark.asyncio
async def test_second_row_ready_taps_confirm_and_stays_upgrade(monkeypatch):
    runner = SequenceRunner()
    runner._upgrade_target = {"type": "lab"}
    runner._loop_mode = "upgrade"
    tap_log = []
    # row1 INSUFFICIENT → row2 READY at green button → verify says gone
    analyze_results = [
        ("INSUFFICIENT_RESOURCES", None),
        ("READY", (897, 629)),
        ("NOT_FOUND", None),
    ]
    close_calls = _patch_lab_common(monkeypatch, runner, tap_log, analyze_results)

    await runner._do_lab_upgrade(_FakeAdb())

    # confirm tapped at the green button position
    assert (897, 629) in tap_log
    # NOT switched to farming — research started
    assert runner._loop_mode == "upgrade"
    assert runner._lab_exhausted_until == 0
    assert runner._upgrade_target is None


@pytest.mark.asyncio
async def test_confirm_retapped_when_button_still_visible(monkeypatch):
    runner = SequenceRunner()
    runner._upgrade_target = {"type": "lab"}
    runner._loop_mode = "upgrade"
    tap_log = []
    # READY → verify still READY (retap) → verify gone
    analyze_results = [
        ("READY", (897, 629)),
        ("READY", (897, 629)),
        ("NOT_FOUND", None),
    ]
    _patch_lab_common(monkeypatch, runner, tap_log, analyze_results)

    await runner._do_lab_upgrade(_FakeAdb())

    assert tap_log.count((897, 629)) == 2  # initial tap + retap
    assert runner._loop_mode == "upgrade"
    assert runner._lab_exhausted_until == 0


@pytest.mark.asyncio
async def test_row_not_found_skips_row_and_farms(monkeypatch):
    runner = SequenceRunner()
    runner._upgrade_target = {"type": "lab"}
    runner._loop_mode = "upgrade"
    tap_log = []
    # no row opens a confirm modal (e.g. label fragments) — all tried → farming
    analyze_results = [("NOT_FOUND", None), ("NOT_FOUND", None)]
    _patch_lab_common(monkeypatch, runner, tap_log, analyze_results)

    await runner._do_lab_upgrade(_FakeAdb())

    # both rows were tried before giving up
    assert (500, 163) in tap_log
    assert (500, 239) in tap_log
    assert runner._loop_mode == "farming"
    assert runner._lab_exhausted_until > 0
    assert runner._upgrade_target is None


@pytest.mark.asyncio
async def test_evaluate_mode_farms_while_lab_exhausted(monkeypatch):
    import time as _time

    runner = SequenceRunner()
    runner._lab_exhausted_until = _time.time() + 900

    class _ExplodingAdb:
        async def screencap(self):
            raise AssertionError("screencap must not be called while lab exhausted")

    result = await runner._evaluate_mode(_ExplodingAdb())
    assert result == "farming"


@pytest.mark.asyncio
async def test_lab_upgrade_no_suggested_closes_panel(monkeypatch):
    runner = SequenceRunner()
    runner._upgrade_target = {"type": "lab"}
    runner._loop_mode = "upgrade"
    monkeypatch.setattr(runner, "_save_lab_confirm_debug", lambda screen: None)

    close_calls = []

    async def fake_tap(adb, x, y, sigma=0):
        pass

    async def fake_delay(*args, **kwargs):
        pass

    async def fake_close_panel(adb, max_attempts=6):
        close_calls.append(1)

    monkeypatch.setattr(runner, "_read_lab_status", lambda screen: "free")
    monkeypatch.setattr(runner, "_close_panel_until_gone", fake_close_panel)
    monkeypatch.setattr(seq_mod, "human_tap", fake_tap)
    monkeypatch.setattr(seq_mod, "human_delay", fake_delay)
    monkeypatch.setattr("backend.db.database.get_session", lambda: _FakeSession())
    monkeypatch.setattr("backend.vision.ocr.find_text", lambda screen, keyword: None)

    await runner._do_lab_upgrade(_FakeAdb())

    assert len(close_calls) == 1
    assert runner._upgrade_target is None
    assert runner._loop_mode == "upgrade"  # mode routing is the caller's job
    assert runner._lab_exhausted_until == 0
