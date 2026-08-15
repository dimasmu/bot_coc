"""Tests for _modal_is_open dim-overlay detection."""

from pathlib import Path

import cv2

import backend.engine.middleware as mw


def _fixture(name: str) -> str:
    return str(Path(__file__).parent / "fixtures" / name)


def test_modal_is_open_detects_open_confirm_modal():
    img = cv2.imread(_fixture("lab_confirm_insufficient.png"))
    assert mw._modal_is_open(img) is True


def test_modal_is_open_clean_home_day():
    img = cv2.imread(_fixture("home_day_clean.png"))
    assert mw._modal_is_open(img) is False


def test_modal_is_open_clean_home_night():
    img = cv2.imread(_fixture("home_night_clean.png"))
    assert mw._modal_is_open(img) is False
