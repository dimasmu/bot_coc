"""Tests for _close_panel_until_gone."""

import cv2
import numpy as np
import pytest

import backend.engine.middleware as mw_mod
import backend.engine.sequence_runner as seq_mod
from backend.engine.sequence_runner import SequenceRunner


def _png_bytes() -> bytes:
    img = np.zeros((720, 1280, 3), dtype=np.uint8)
    _, buf = cv2.imencode(".png", img)
    return buf.tobytes()


class _FakeAdb:
    def __init__(self, screens):
        self._screens = screens

    async def screencap(self):
        return self._screens.pop(0) if self._screens else None


class _FakeRoi:
    x_pos = 1199
    y_pos = 25
    width = 61
    height = 57


class _FakeSession:
    def __init__(self, roi=_FakeRoi()):
        self._roi = roi  # pass False for "ROI not calibrated"

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


@pytest.mark.asyncio
async def test_close_panel_taps_x_until_gone(runner, monkeypatch):
    taps = []

    async def fake_tap(adb, x, y, sigma=0):
        taps.append((x, y))

    async def fake_delay(*args, **kwargs):
        pass

    def fake_modal_open(img):
        return len(taps) < 2  # open until two close taps land

    monkeypatch.setattr(seq_mod, "human_tap", fake_tap)
    monkeypatch.setattr(seq_mod, "human_delay", fake_delay)
    monkeypatch.setattr(mw_mod, "_modal_is_open", fake_modal_open)
    monkeypatch.setattr(mw_mod, "_find_close_x_button", lambda hsv: (1131, 56))

    result = await runner._close_panel_until_gone(
        _FakeAdb([_png_bytes()] * 4))
    assert result is True
    assert taps == [(1131, 56), (1131, 56)]


@pytest.mark.asyncio
async def test_close_panel_uses_roi_when_no_x(runner, monkeypatch):
    taps = []

    async def fake_tap(adb, x, y, sigma=0):
        taps.append((x, y))

    async def fake_delay(*args, **kwargs):
        pass

    def fake_modal_open(img):
        return len(taps) < 1

    monkeypatch.setattr(seq_mod, "human_tap", fake_tap)
    monkeypatch.setattr(seq_mod, "human_delay", fake_delay)
    monkeypatch.setattr(mw_mod, "_modal_is_open", fake_modal_open)
    monkeypatch.setattr(mw_mod, "_find_close_x_button", lambda hsv: None)
    monkeypatch.setattr(seq_mod, "get_session", lambda: _FakeSession())

    result = await runner._close_panel_until_gone(
        _FakeAdb([_png_bytes()] * 3))
    assert result is True
    assert taps == [(1229, 53)]  # btn_close_universal ROI center


@pytest.mark.asyncio
async def test_close_panel_taps_dim_strip_without_roi(runner, monkeypatch):
    taps = []

    async def fake_tap(adb, x, y, sigma=0):
        taps.append((x, y))

    async def fake_delay(*args, **kwargs):
        pass

    def fake_modal_open(img):
        return len(taps) < 1

    monkeypatch.setattr(seq_mod, "human_tap", fake_tap)
    monkeypatch.setattr(seq_mod, "human_delay", fake_delay)
    monkeypatch.setattr(mw_mod, "_modal_is_open", fake_modal_open)
    monkeypatch.setattr(mw_mod, "_find_close_x_button", lambda hsv: None)
    monkeypatch.setattr(seq_mod, "get_session", lambda: _FakeSession(False))

    result = await runner._close_panel_until_gone(
        _FakeAdb([_png_bytes()] * 3))
    assert result is True
    assert taps == [(30, 400)]  # dimmed strip outside the panel


@pytest.mark.asyncio
async def test_close_panel_clean_screen_no_taps(runner, monkeypatch):
    taps = []

    async def fake_tap(adb, x, y, sigma=0):
        taps.append((x, y))

    monkeypatch.setattr(seq_mod, "human_tap", fake_tap)
    monkeypatch.setattr(mw_mod, "_modal_is_open", lambda img: False)

    result = await runner._close_panel_until_gone(_FakeAdb([_png_bytes()]))
    assert result is True
    assert taps == []


@pytest.mark.asyncio
async def test_close_panel_screencap_failure_returns_false(runner, monkeypatch):
    async def fake_tap(adb, x, y, sigma=0):
        pass

    monkeypatch.setattr(seq_mod, "human_tap", fake_tap)

    result = await runner._close_panel_until_gone(_FakeAdb([]))
    assert result is False
