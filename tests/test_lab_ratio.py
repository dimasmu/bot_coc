"""Tests for read_ratio OCR helper."""

import cv2
import numpy as np

import backend.vision.ocr as ocr


def _make_blank_screen() -> bytes:
    img = np.zeros((720, 1280, 3), dtype=np.uint8)
    _, buf = cv2.imencode(".png", img)
    return buf.tobytes()


def test_read_ratio_invalid_bounds_returns_none():
    screen = _make_blank_screen()
    assert ocr.read_ratio(screen, -100, -100, 50, 50) is None


def test_read_ratio_unreadable_returns_none(monkeypatch):
    screen = _make_blank_screen()

    class FakeReader:
        def readtext(self, *args, **kwargs):
            return []

    monkeypatch.setattr(ocr, "_get_reader", lambda: FakeReader())
    monkeypatch.setattr(ocr, "read_number", lambda s, x, y, w, h, roi_name="": None)

    assert ocr.read_ratio(screen, 400, 20, 80, 30) is None


def test_read_ratio_split_fallback_returns_pair(monkeypatch):
    screen = _make_blank_screen()

    class FakeReader:
        def readtext(self, *args, **kwargs):
            return []

    def fake_read_number(s, x, y, w, h, roi_name=""):
        return 0 if x < 440 else 1

    monkeypatch.setattr(ocr, "_get_reader", lambda: FakeReader())
    monkeypatch.setattr(ocr, "read_number", fake_read_number)

    assert ocr.read_ratio(screen, 400, 20, 80, 30) == (0, 1)


def test_read_ratio_reads_real_lab_badge():
    """Integration: the calibrated lab_status ROI on a real home-screen
    top bar (free lab, badge shows '1/1') must OCR as (1, 1).

    Fixture is the top 100px strip of storage/debug/lab_debug_current.png.
    """
    from pathlib import Path

    screen = Path(__file__).parent / "fixtures" / "lab_badge_free.png"
    assert ocr.read_ratio(screen.read_bytes(), 474, 27, 58, 34,
                          roi_name="lab_status") == (1, 1)


def test_parse_ratio_matches_simple():
    assert ocr._parse_ratio(["0/1"]) == (0, 1)


def test_parse_ratio_matches_spaced():
    assert ocr._parse_ratio(["0 / 1"]) == (0, 1)


def test_parse_ratio_matches_joined_tokens():
    assert ocr._parse_ratio(["2", "/", "5"]) == (2, 5)


def test_parse_ratio_no_match_returns_none():
    assert ocr._parse_ratio(["no digits"]) is None


def test_parse_ratio_empty_returns_none():
    assert ocr._parse_ratio([]) is None
