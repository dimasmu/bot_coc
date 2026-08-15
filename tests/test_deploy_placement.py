"""Tests untuk deteksi bar deploy (backend/vision/deploy.py) dan flow
penempatan bangunan baru (_do_new_building_purchase).

Fixture live 1280x720: deploy_valid.png (centang hijau) dan
deploy_blocked.png (centang abu — terhalang obstacle).
"""

from pathlib import Path

import cv2
import pytest

import backend.engine.sequence_runner as seq_mod
from backend.engine.sequence_runner import SequenceRunner
from backend.vision import deploy


def _fixture(name: str) -> str:
    return str(Path(__file__).parent / "fixtures" / name)


# ── Detector: backend/vision/deploy ──────────────────────────────────


def test_find_deploy_bar_valid_capture():
    img = cv2.imread(_fixture("deploy_valid.png"))
    bar = deploy.find_deploy_bar(img)
    assert bar is not None
    bx, by, bw, bh = bar
    assert 40 <= bw <= 120 and 40 <= bh <= 110
    # kotak bar berpusat di centang hijau — harus memuat posisi centang
    # terkalibrasi capture ini ~(518,309)
    ck, _ = deploy.deploy_button_centers(img, bar)
    assert abs(ck[0] - 518) < 40 and abs(ck[1] - 309) < 40


def test_find_deploy_bar_blocked_capture():
    img = cv2.imread(_fixture("deploy_blocked.png"))
    bar = deploy.find_deploy_bar(img)
    assert bar is not None
    bx, by, bw, bh = bar
    assert abs(bx - 329) < 15 and abs(by - 446) < 15


def test_find_deploy_bar_none_on_clean_home():
    for name in ("home_day_clean.png", "home_night_clean.png"):
        img = cv2.imread(_fixture(name))
        assert deploy.find_deploy_bar(img) is None


def test_checkmark_state_green_on_valid():
    img = cv2.imread(_fixture("deploy_valid.png"))
    bar = deploy.find_deploy_bar(img)
    assert deploy.deploy_checkmark_state(img, bar) == "green"


def test_checkmark_state_gray_on_blocked():
    img = cv2.imread(_fixture("deploy_blocked.png"))
    bar = deploy.find_deploy_bar(img)
    assert deploy.deploy_checkmark_state(img, bar) == "gray"


def test_button_centers_inside_bar():
    img = cv2.imread(_fixture("deploy_valid.png"))
    bar = deploy.find_deploy_bar(img)
    ck, xb = deploy.deploy_button_centers(img, bar)
    bx, by, bw, bh = bar
    for (cx, cy) in (ck, xb):
        assert bx <= cx <= bx + bw
        assert by <= cy <= by + bh
    # keduanya harus sebaris (baris tombol yang sama)
    assert abs(ck[1] - xb[1]) < 40


# ── Flow: _do_new_building_purchase ──────────────────────────────────


class _FakeAdb:
    """screencap queue + tap/swipe recording."""

    def __init__(self, screens):
        self.screens = list(screens)
        self.taps = []
        self.swipes = []

    async def screencap(self):
        if not self.screens:
            return None
        return self.screens.pop(0)

    async def tap(self, x, y):
        self.taps.append((x, y))
        return True

    async def swipe(self, x1, y1, x2, y2, duration_ms=200):
        self.swipes.append((x1, y1, x2, y2))
        return True


def _patch_purchase_common(monkeypatch, runner):
    async def fake_delay(*args, **kwargs):
        pass

    monkeypatch.setattr(runner, "_save_deploy_debug", lambda screen: None)
    monkeypatch.setattr(seq_mod, "human_delay", fake_delay)
    monkeypatch.setattr(
        "backend.engine.middleware.find_shop_arrow_cv",
        lambda img: (756, 311),
    )
    # Phase B: shop tab check — pretend we left the shop
    monkeypatch.setattr(
        "backend.vision.matching.match_template",
        lambda cap, path, threshold=0.6: None,
    )


def _png(path):
    return Path(_fixture(path)).read_bytes()


@pytest.mark.asyncio
async def test_purchase_places_immediately_when_green(monkeypatch):
    runner = SequenceRunner()
    _patch_purchase_common(monkeypatch, runner)

    adb = _FakeAdb([
        _png("deploy_valid.png"),   # Phase A (arrow patched)
        _png("deploy_valid.png"),   # Phase B shop check
        _png("deploy_valid.png"),   # debug save
        _png("deploy_valid.png"),   # attempt 0: green
        _png("home_day_clean.png"),  # verify after tap: no bar
    ])
    result = await runner._do_new_building_purchase(adb)

    assert result is True
    assert adb.swipes == []  # tidak perlu geser
    # tap centang hijau terkalibrasi ~(518,309)
    ck_taps = [t for t in adb.taps if 480 <= t[0] <= 560 and 280 <= t[1] <= 340]
    assert ck_taps, f"centang tidak ditap — taps: {adb.taps}"


@pytest.mark.asyncio
async def test_purchase_drags_to_next_candidate_when_blocked(monkeypatch):
    runner = SequenceRunner()
    _patch_purchase_common(monkeypatch, runner)

    adb = _FakeAdb([
        _png("deploy_valid.png"),    # Phase A
        _png("deploy_valid.png"),    # Phase B
        _png("deploy_valid.png"),    # debug save
        _png("deploy_blocked.png"),  # attempt 0: blocked → geser
        _png("deploy_valid.png"),    # attempt 1: green → tap
        _png("home_day_clean.png"),  # verify: no bar
    ])
    result = await runner._do_new_building_purchase(adb)

    assert result is True
    assert len(adb.swipes) == 1
    # drag mulai dari ghost (di bawah bar blocked di (329,446,66,73))
    x1, y1, x2, y2 = adb.swipes[0]
    assert 300 <= x1 <= 400 and 500 <= y1 <= 600
    # kandidat pertama grid (300, 220)
    assert (x2, y2) == (300, 220)


@pytest.mark.asyncio
async def test_purchase_cancels_and_returns_false_when_all_blocked(monkeypatch):
    runner = SequenceRunner()
    _patch_purchase_common(monkeypatch, runner)

    monkeypatch.setattr(
        "backend.vision.deploy.find_deploy_bar",
        lambda img: (329, 446, 66, 73),
    )
    monkeypatch.setattr(
        "backend.vision.deploy.deploy_checkmark_state",
        lambda img, bar: "gray",
    )

    screens = [_png("deploy_valid.png"), _png("deploy_valid.png")]
    # debug save (1) + 25 percobaan (cek awal + 24 kandidat) + 1 cancel — semua blocked
    for _ in range(27):
        screens.append(_png("deploy_blocked.png"))
    adb = _FakeAdb(screens)

    result = await runner._do_new_building_purchase(adb)

    assert result is False
    assert len(adb.swipes) == 24  # semua kandidat dicoba
    # tap terakhir = tombol X cancel (dari fixture blocked, x > ck)
    last_taps = [t for t in adb.taps if 350 <= t[0] <= 420]
    assert last_taps, f"tombol X tidak ditap — taps: {adb.taps[-3:]}"


@pytest.mark.asyncio
async def test_purchase_returns_false_when_no_deploy_bar(monkeypatch):
    runner = SequenceRunner()
    _patch_purchase_common(monkeypatch, runner)

    monkeypatch.setattr(
        "backend.vision.deploy.find_deploy_bar",
        lambda img: None,
    )

    adb = _FakeAdb([
        _png("deploy_valid.png"),   # Phase A
        _png("deploy_valid.png"),   # Phase B
        _png("home_day_clean.png"),  # attempt 0: no bar
    ])
    result = await runner._do_new_building_purchase(adb)

    assert result is False
    assert adb.swipes == []
