"""Tests for confirm-button color validation (red cost = insufficient)."""

from pathlib import Path

import cv2
import numpy as np

import backend.vision as vision


def _blank_rgb():
    return np.zeros((720, 1280, 3), dtype=np.uint8)


def test_confirm_cost_is_red_detects_red_digits():
    img = _blank_rgb()
    img[600:700, 800:1000] = (0, 200, 80)  # green button body
    img[620:660, 840:900] = (0, 0, 255)    # red cost digits inside it
    assert vision._confirm_cost_is_red(img, (800, 600, 200, 100)) is True


def test_confirm_cost_is_red_ignores_plain_green_button():
    img = _blank_rgb()
    img[600:700, 800:1000] = (0, 200, 80)
    assert vision._confirm_cost_is_red(img, (800, 600, 200, 100)) is False


def test_confirm_cost_is_red_empty_box_is_false():
    img = _blank_rgb()
    assert vision._confirm_cost_is_red(img, (-100, -100, 50, 50)) is False


def test_analyze_upgrade_confirm_insufficient_on_real_modal():
    """Integration: modal dengan angka biaya merah harus diklasifikasi
    INSUFFICIENT_RESOURCES oleh detektor generic."""
    fixture = Path(__file__).parent / "fixtures" / "lab_confirm_insufficient.png"
    img = cv2.imread(str(fixture))
    status, pos = vision.analyze_upgrade_confirm_button(img)
    assert status == "INSUFFICIENT_RESOURCES"
    assert pos is None
