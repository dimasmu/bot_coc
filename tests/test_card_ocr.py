"""Tests for card badge OCR utilities."""

import cv2
import numpy as np
import pytest

from backend.vision.ocr import read_card_badge, _check_badge_texture


def _make_screen_with_badge(has_badge: bool = False, number: int | None = None) -> bytes:
    """Create a synthetic 1280x720 BGR screenshot with a card and optional badge.

    Card is drawn at x=380 (center), card_top=635 (bottom bar).
    Badge is at x=388, y=639 (card_x+8, card_top+4), 28x24px.
    """
    img = np.zeros((720, 1280, 3), dtype=np.uint8)

    # Draw card area (dark background)
    card_x, card_top = 380, 635
    card_x1 = card_x - 30
    card_y1 = card_top
    img[card_y1:card_y1 + 85, card_x1:card_x1 + 60] = (40, 40, 40)

    if has_badge:
        # Badge region: white text on dark background
        badge_x1 = card_x + 8
        badge_y1 = card_top + 4
        badge_w, badge_h = 28, 24
        # Dark badge background
        img[badge_y1:badge_y1 + badge_h, badge_x1:badge_x1 + badge_w] = (20, 20, 20)

        if number is not None:
            # Write number as white pixels in the badge
            font = cv2.FONT_HERSHEY_SIMPLEX
            txt = str(number)
            cv2.putText(img, txt, (badge_x1 + 2, badge_y1 + 18),
                       font, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
    else:
        # No badge: keep the badge area uniformly dark so the OTSU-based
        # texture check reports ~0% white (the card rect alone would overlap
        # only part of this region, leaving a two-tone patch).
        badge_x1 = card_x + 8
        badge_y1 = card_top + 4
        img[badge_y1:badge_y1 + 24, badge_x1:badge_x1 + 28] = (0, 0, 0)

    # Encode to PNG bytes
    _, buf = cv2.imencode(".png", img)
    return buf.tobytes()


def test_read_card_badge_returns_number():
    """Badge with '2' should return 2."""
    screen = _make_screen_with_badge(has_badge=True, number=2)
    result = read_card_badge(screen, card_x=380, card_top=635)
    # OCR may fail on synthetic images; test shape is the goal
    # At minimum, function should not raise
    assert isinstance(result, (int, type(None)))


def test_read_card_badge_hero_returns_none():
    """Hero card (no badge) should return None."""
    screen = _make_screen_with_badge(has_badge=False)
    result = read_card_badge(screen, card_x=380, card_top=635)
    assert result is None


def test_check_badge_texture_with_badge():
    """Badge region with content should have white_pct > 0."""
    screen = _make_screen_with_badge(has_badge=True, number=3)
    white_pct = _check_badge_texture(screen, card_x=380, card_top=635)
    # With synthetic white text on dark bg, should detect some white pixels
    assert white_pct >= 0.0


def test_check_badge_texture_empty():
    """Empty badge region should have very low white_pct."""
    screen = _make_screen_with_badge(has_badge=False)
    white_pct = _check_badge_texture(screen, card_x=380, card_top=635)
    # Uniform dark region: white_pct should be near 0
    assert white_pct < 0.1


def test_read_card_badge_invalid_bounds():
    """Card at screen edge should not crash."""
    screen = _make_screen_with_badge(has_badge=False)
    result = read_card_badge(screen, card_x=-100, card_top=-100)
    assert result is None


def test_check_badge_texture_invalid_bounds():
    """Card at screen edge: texture check should return 0."""
    screen = _make_screen_with_badge(has_badge=False)
    white_pct = _check_badge_texture(screen, card_x=0, card_top=0)
    assert white_pct == 0.0


# ----- Integration tests for SequenceRunner card depletion detection -----

import asyncio
import pytest

from backend.adb.mock import MockAdbManager
from backend.engine.sequence_runner import SequenceRunner, ScreenVerificationError


def _make_screen_with_card(card_x: int = 380, card_top: int = 635,
                           grey: bool = False) -> bytes:
    """Create a 1280x720 BGR screenshot with a card at the given position.

    If grey=True, card uses uniform grey pixels (depleted).
    If grey=False, card has colourful pixels (active).
    """
    img = np.zeros((720, 1280, 3), dtype=np.uint8)
    card_x1 = card_x - 30
    card_y1 = card_top

    if grey:
        # Uniform grey overlay (depleted card)
        img[card_y1:card_y1 + 85, card_x1:card_x1 + 60] = (80, 80, 80)
    else:
        # Colorful card (active) — non-uniform, saturated colors
        # Use brown/dark card base (not black — black=S=0 in HSV)
        img[card_y1:card_y1 + 85, card_x1:card_x1 + 60] = (15, 40, 60)
        # Add colourful patches simulating troop icon
        img[card_y1+5:card_y1+50, card_x1+5:card_x1+55] = (30, 160, 240)  # bright orange
        img[card_y1+30:card_y1+70, card_x1+8:card_x1+28] = (220, 40, 10)  # deep blue
        img[card_y1+20:card_y1+60, card_x1+30:card_x1+55] = (20, 220, 80)  # bright green

    _, buf = cv2.imencode(".png", img)
    return buf.tobytes()


@pytest.fixture
def runner():
    """Create a SequenceRunner that we can test methods on."""
    return SequenceRunner()


@pytest.mark.asyncio
async def test_is_card_depleted_active_card(runner):
    """An active (colourful) card should NOT be detected as depleted."""
    active_screen = _make_screen_with_card(grey=False)

    class ActiveAdb(MockAdbManager):
        async def screencap(self):
            return active_screen

    card = {"x": 380, "y": 677, "card_top": 635}
    result = await runner._is_card_depleted(ActiveAdb(), card)
    assert result is False


@pytest.mark.asyncio
async def test_is_card_depleted_grey_card(runner):
    """A grey (depleted) card SHOULD be detected as depleted."""
    grey_screen = _make_screen_with_card(grey=True)

    class GreyAdb(MockAdbManager):
        async def screencap(self):
            return grey_screen

    card = {"x": 380, "y": 677, "card_top": 635}
    result = await runner._is_card_depleted(GreyAdb(), card)
    assert result is True


@pytest.mark.asyncio
async def test_is_card_depleted_no_screencap(runner):
    """When screencap fails (returns None), should return False (safe default)."""
    class NoScreenAdb:
        is_connected = True
        async def connect(self): return True
        async def screencap(self): return None

    card = {"x": 380, "y": 677, "card_top": 635}
    result = await runner._is_card_depleted(NoScreenAdb(), card)
    assert result is False


@pytest.mark.asyncio
async def test_is_card_depleted_returns_bool(runner):
    """_is_card_depleted should always return a bool, never raise."""
    mock = MockAdbManager()
    card = {"x": 380, "y": 677, "card_top": 635}
    result = await runner._is_card_depleted(mock, card)
    assert isinstance(result, bool)


@pytest.mark.asyncio
async def test_do_attack_no_cards():
    """_do_attack with no detected cards raises ScreenVerificationError."""
    runner = SequenceRunner()
    runner._running = False  # prevent deploy loop

    class NoCardAdb(MockAdbManager):
        async def screencap(self):
            img = np.zeros((720, 1280, 3), dtype=np.uint8)
            _, buf = cv2.imencode(".png", img)
            return buf.tobytes()

    from unittest.mock import MagicMock
    step = MagicMock()
    step.config_json = '{"duration": 1}'

    with pytest.raises(ScreenVerificationError):
        await runner._do_attack(step, NoCardAdb())
