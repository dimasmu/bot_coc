"""Tests for _read_lab_status."""

import cv2
import numpy as np
import pytest

import backend.engine.sequence_runner as seq_mod
from backend.engine.sequence_runner import SequenceRunner


def _make_blank_screen() -> bytes:
    img = np.zeros((720, 1280, 3), dtype=np.uint8)
    _, buf = cv2.imencode(".png", img)
    return buf.tobytes()


class _FakeRoi:
    x_pos = 400
    y_pos = 20
    width = 80
    height = 30


class _FakeSession:
    def __init__(self, roi):
        self._roi = roi

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def query(self, model):
        return self

    def filter_by(self, **kwargs):
        return self

    def first(self):
        return self._roi


@pytest.fixture
def runner():
    return SequenceRunner()


def test_read_lab_status_unknown_when_roi_missing(runner, monkeypatch):
    monkeypatch.setattr(seq_mod, "get_session", lambda: _FakeSession(None))
    assert runner._read_lab_status(_make_blank_screen()) == "unknown"


def test_read_lab_status_unknown_when_ratio_none(runner, monkeypatch):
    monkeypatch.setattr(seq_mod, "get_session", lambda: _FakeSession(_FakeRoi()))
    monkeypatch.setattr(seq_mod, "read_ratio", lambda *a, **k: None)
    assert runner._read_lab_status(_make_blank_screen()) == "unknown"


def test_read_lab_status_free_when_used_zero(runner, monkeypatch):
    calls = []

    def fake_read_ratio(*args, **kwargs):
        calls.append((args, kwargs))
        return (0, 1)

    monkeypatch.setattr(seq_mod, "get_session", lambda: _FakeSession(_FakeRoi()))
    monkeypatch.setattr(seq_mod, "read_ratio", fake_read_ratio)

    assert runner._read_lab_status(_make_blank_screen()) == "free"
    assert calls[0][1].get("roi_name") == "lab_status"
    assert calls[0][0][1:5] == (400, 20, 80, 30)


def test_read_lab_status_busy_when_used_nonzero(runner, monkeypatch):
    calls = []

    def fake_read_ratio(*args, **kwargs):
        calls.append((args, kwargs))
        return (1, 1)

    monkeypatch.setattr(seq_mod, "get_session", lambda: _FakeSession(_FakeRoi()))
    monkeypatch.setattr(seq_mod, "read_ratio", fake_read_ratio)

    assert runner._read_lab_status(_make_blank_screen()) == "busy"
    assert calls[0][1].get("roi_name") == "lab_status"
    assert calls[0][0][1:5] == (400, 20, 80, 30)


@pytest.mark.asyncio
async def test_tap_close_universal_taps_roi_center(runner, monkeypatch):
    taps = []

    async def fake_tap(adb, x, y, sigma=0):
        taps.append((x, y))

    monkeypatch.setattr(seq_mod, "get_session", lambda: _FakeSession(_FakeRoi()))
    monkeypatch.setattr(seq_mod, "human_tap", fake_tap)

    await runner._tap_close_universal(None)
    assert taps == [(440, 35)]  # 400 + 80//2, 20 + 30//2
