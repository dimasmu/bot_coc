"""Tests for _do_lab_upgrade confirm handling."""

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


@pytest.mark.asyncio
async def test_lab_upgrade_insufficient_resources_closes_twice_and_farming(monkeypatch):
    runner = SequenceRunner()
    runner._upgrade_target = {"type": "lab"}
    runner._loop_mode = "upgrade"
    monkeypatch.setattr(runner, "_save_lab_confirm_debug", lambda screen: None)

    close_calls = []

    async def fake_tap(adb, x, y, sigma=0):
        pass

    async def fake_delay(*args, **kwargs):
        pass

    async def fake_close(adb):
        close_calls.append(1)

    monkeypatch.setattr(runner, "_read_lab_status", lambda screen: "free")
    monkeypatch.setattr(runner, "_tap_close_universal", fake_close)
    monkeypatch.setattr(seq_mod, "human_tap", fake_tap)
    monkeypatch.setattr(seq_mod, "human_delay", fake_delay)
    monkeypatch.setattr("backend.db.database.get_session", lambda: _FakeSession())
    monkeypatch.setattr("backend.vision.ocr.find_text", lambda screen, keyword: (500, 200))
    monkeypatch.setattr(seq_mod, "analyze_confirm_button", lambda img: ("INSUFFICIENT_RESOURCES", None))

    await runner._do_lab_upgrade(_FakeAdb())

    assert len(close_calls) == 2
    assert runner._loop_mode == "farming"
    assert runner._upgrade_target is None


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

    async def fake_close(adb):
        close_calls.append(1)

    monkeypatch.setattr(runner, "_read_lab_status", lambda screen: "free")
    monkeypatch.setattr(runner, "_tap_close_universal", fake_close)
    monkeypatch.setattr(seq_mod, "human_tap", fake_tap)
    monkeypatch.setattr(seq_mod, "human_delay", fake_delay)
    monkeypatch.setattr("backend.db.database.get_session", lambda: _FakeSession())
    monkeypatch.setattr("backend.vision.ocr.find_text", lambda screen, keyword: None)

    await runner._do_lab_upgrade(_FakeAdb())

    assert len(close_calls) == 1
    assert runner._upgrade_target is None
    assert runner._loop_mode == "upgrade"  # mode routing is the caller's job
