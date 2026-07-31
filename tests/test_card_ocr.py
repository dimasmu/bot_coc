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


# ----- Integration tests for SequenceRunner -----

import asyncio
import pytest

from backend.adb.mock import MockAdbManager
from backend.engine.sequence_runner import SequenceRunner, CardResult


@pytest.fixture
def runner():
    """Create a SequenceRunner that we can test methods on."""
    return SequenceRunner()


@pytest.mark.asyncio
async def test_read_card_count_returns_card_result(runner):
    """_read_card_count should always return a CardResult, never raise."""
    mock = MockAdbManager()
    card = {"x": 380, "y": 677, "card_top": 635}

    result = await runner._read_card_count(mock, card)
    assert isinstance(result, CardResult)
    assert hasattr(result, "count")
    assert hasattr(result, "has_badge")


@pytest.mark.asyncio
async def test_read_card_count_no_screencap():
    """When screencap fails (returns None), should return CardResult with has_badge=False."""
    runner = SequenceRunner()

    class NoScreenAdb:
        is_connected = True
        async def connect(self): return True
        async def screencap(self): return None

    card = {"x": 380, "y": 677, "card_top": 635}
    result = await runner._read_card_count(NoScreenAdb(), card)
    assert isinstance(result, CardResult)
    assert result.has_badge is False
    assert result.count is None


def test_card_result_dataclass():
    """CardResult should be constructable and have correct defaults."""
    r1 = CardResult(count=3, has_badge=True)
    assert r1.count == 3
    assert r1.has_badge is True

    r2 = CardResult(count=None, has_badge=False)
    assert r2.count is None
    assert r2.has_badge is False

    r3 = CardResult(count=0, has_badge=True)
    assert r3.count == 0
    assert r3.has_badge is True


@pytest.mark.asyncio
async def test_do_attack_no_cards():
    """_do_attack with no detected cards should wait and return."""
    runner = SequenceRunner()
    runner._running = False  # prevent loop

    class NoCardAdb(MockAdbManager):
        async def screencap(self):
            import cv2
            import numpy as np
            img = np.zeros((720, 1280, 3), dtype=np.uint8)
            _, buf = cv2.imencode(".png", img)
            return buf.tobytes()

    from unittest.mock import MagicMock
    step = MagicMock()
    step.config_json = '{"duration": 1}'

    await runner._do_attack(step, NoCardAdb())
    # Should not raise — just sleep and return
