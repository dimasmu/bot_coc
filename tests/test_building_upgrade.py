"""Tests untuk confirm upgrade bangunan (_do_upgrade_execute Phase 3).

Detektor generic analyze_upgrade_confirm_button (HSV wide-button
kanan-bawah) menggantikan template matcher lama yang meleset ke
(986,547) padahal tombol asli di (897,629).
"""

from pathlib import Path

import cv2
import pytest

import backend.engine.sequence_runner as seq_mod
from backend.engine.sequence_runner import SequenceRunner
import backend.vision as vision


def _fixture(name: str) -> str:
    return str(Path(__file__).parent / "fixtures" / name)


def test_building_confirm_ready_fixture():
    """Panel upgrade Cannon (capture live) — tombol hijau di (897,629)."""
    img = cv2.imread(_fixture("building_confirm_ready.png"))
    status, pos = vision.analyze_upgrade_confirm_button(img)
    assert status == "READY"
    x, y = pos
    assert 797 <= x <= 998 and 587 <= y <= 672


# ── Flow: _do_upgrade_execute building path ──────────────────────────


class _FakeRoi:
    def __init__(self, name="builder_menu", x=628, y=18, w=60, h=44):
        self.roi_name = name
        self.x_pos = x
        self.y_pos = y
        self.width = w
        self.height = h
        self.value = "300000"


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
    def __init__(self, screens):
        self.screens = list(screens)
        self.taps = []

    async def screencap(self):
        if not self.screens:
            return None
        return self.screens.pop(0)

    async def tap(self, x, y):
        self.taps.append((x, y))
        return True


def _png(path):
    return Path(_fixture(path)).read_bytes()


def _patch_building_common(monkeypatch, runner, analyze_results):
    async def fake_delay(*args, **kwargs):
        pass

    async def fake_close_panel(adb, max_attempts=6):
        pass

    results = iter(analyze_results)

    monkeypatch.setattr(runner, "_close_panel_until_gone", fake_close_panel)
    monkeypatch.setattr(seq_mod, "human_delay", fake_delay)
    monkeypatch.setattr("backend.db.database.get_session", lambda: _FakeSession())
    monkeypatch.setattr(
        "backend.vision.ocr.find_text", lambda screen, keyword: (575, 201)
    )
    # Phase 1/2: shop-tab check → None (bukan shop); hammer/cog → ditemukan
    def _fake_match(cap, path, threshold=0.6):
        if "shop_tab" in str(path):
            return None
        return (637, 560)

    monkeypatch.setattr(
        "backend.vision.matching.match_template", _fake_match
    )
    monkeypatch.setattr(
        seq_mod, "analyze_upgrade_confirm_button", lambda img: next(results)
    )


@pytest.mark.asyncio
async def test_building_upgrade_taps_green_confirm_and_verifies(monkeypatch):
    runner = SequenceRunner()
    runner._upgrade_target = {"type": "building", "resources": {}}
    tap_log = []
    _patch_building_common(monkeypatch, runner, [
        ("READY", (897, 629)),   # Phase 3 first analyze
        ("NOT_FOUND", None),     # verify after tap: button gone
    ])

    async def fake_tap(adb, x, y, sigma=0):
        tap_log.append((x, y))

    monkeypatch.setattr(seq_mod, "human_tap", fake_tap)

    adb = _FakeAdb([_png("building_confirm_ready.png")] * 10)
    await runner._do_upgrade_execute(adb)

    # confirm ditap di posisi tombol hijau
    assert (897, 629) in tap_log
    # upgrade target dibersihkan setelah sukses
    assert runner._upgrade_target is None


@pytest.mark.asyncio
async def test_building_upgrade_retaps_when_button_persists(monkeypatch):
    runner = SequenceRunner()
    runner._upgrade_target = {"type": "building", "resources": {}}
    tap_log = []
    # READY → verify still READY (retap) → verify gone
    _patch_building_common(monkeypatch, runner, [
        ("READY", (897, 629)),
        ("READY", (897, 629)),
        ("NOT_FOUND", None),
    ])

    async def fake_tap(adb, x, y, sigma=0):
        tap_log.append((x, y))

    monkeypatch.setattr(seq_mod, "human_tap", fake_tap)

    adb = _FakeAdb([_png("building_confirm_ready.png")] * 10)
    await runner._do_upgrade_execute(adb)

    assert tap_log.count((897, 629)) == 2  # tap + retap
    assert runner._upgrade_target is None


@pytest.mark.asyncio
async def test_building_upgrade_insufficient_closes_and_farms(monkeypatch):
    runner = SequenceRunner()
    runner._upgrade_target = {"type": "building", "resources": {}}
    runner._loop_mode = "upgrade"
    tap_log = []
    _patch_building_common(monkeypatch, runner, [
        ("INSUFFICIENT_RESOURCES", None),
    ])

    async def fake_tap(adb, x, y, sigma=0):
        tap_log.append((x, y))

    monkeypatch.setattr(seq_mod, "human_tap", fake_tap)

    adb = _FakeAdb([_png("building_confirm_ready.png")] * 10)
    await runner._do_upgrade_execute(adb)

    assert (897, 629) not in tap_log  # confirm tidak ditap
    # modal ditutup via btn_close_universal (buka menu + tutup menu + tutup modal)
    assert tap_log.count((658, 40)) == 3
    assert runner._loop_mode == "farming"
    assert runner._upgrade_target is None
