"""Tests for analyze_lab_confirm_button — lab research confirm modal.

The building-modal template matcher mislocates the lab Research button
(real captures: template lands at (986,547) while the button is at
(897,629)), so the lab flow uses a dedicated HSV-green detector in a
tight bottom-right ROI.
"""

from pathlib import Path

import cv2
import numpy as np

import backend.vision as vision


def _fixture(name: str) -> str:
    return str(Path(__file__).parent / "fixtures" / name)


# Real button box measured across 5 lab captures: (797,587)-(998,672)
_BTN_BOX = (797, 587, 998, 672)


def test_ready_fixture_returns_position_inside_button():
    img = cv2.imread(_fixture("lab_confirm_ready.png"))
    status, pos = vision.analyze_lab_confirm_button(img)
    assert status == "READY"
    assert pos is not None
    x, y = pos
    assert _BTN_BOX[0] <= x <= _BTN_BOX[2]
    assert _BTN_BOX[1] <= y <= _BTN_BOX[3]


def test_insufficient_fixture_detects_red_cost():
    img = cv2.imread(_fixture("lab_confirm_insufficient.png"))
    status, pos = vision.analyze_lab_confirm_button(img)
    assert status == "INSUFFICIENT_RESOURCES"
    assert pos is None


def test_no_green_button_returns_not_found():
    img = np.zeros((720, 1280, 3), dtype=np.uint8)
    status, pos = vision.analyze_lab_confirm_button(img)
    assert status == "NOT_FOUND"
    assert pos is None
