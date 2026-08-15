"""Tests for _verify_step_screen screen expectations."""

import pytest

from backend.engine.sequence_runner import SequenceRunner


class _FakeAdb:
    pass


@pytest.fixture
def runner():
    return SequenceRunner()


@pytest.mark.asyncio
async def test_wait_step_passes_without_screen_check(runner, monkeypatch):
    calls = []

    async def fake_home(adb):
        calls.append("home")
        return False

    monkeypatch.setattr(runner, "_is_home_screen", fake_home)
    assert await runner._verify_step_screen(_FakeAdb(), "wait") is True
    assert calls == []


@pytest.mark.asyncio
async def test_mid_attack_taps_pass_without_screen_check(runner, monkeypatch):
    calls = []

    async def fake_home(adb):
        calls.append("home")
        return False

    monkeypatch.setattr(runner, "_is_home_screen", fake_home)
    for roi in ("btn_find_match", "myarmy_btn_attack"):
        assert await runner._verify_step_screen(
            _FakeAdb(), "tap", roi_name=roi) is True
    assert calls == []


@pytest.mark.asyncio
async def test_tap_btn_attack_requires_home(runner, monkeypatch):
    async def fake_home_true(adb):
        return True

    async def fake_home_false(adb):
        return False

    monkeypatch.setattr(runner, "_is_home_screen", fake_home_true)
    assert await runner._verify_step_screen(
        _FakeAdb(), "tap", roi_name="btn_attack") is True

    monkeypatch.setattr(runner, "_is_home_screen", fake_home_false)
    assert await runner._verify_step_screen(
        _FakeAdb(), "tap", roi_name="btn_attack") is False


@pytest.mark.asyncio
async def test_upgrade_steps_require_home(runner, monkeypatch):
    async def fake_home_false(adb):
        return False

    monkeypatch.setattr(runner, "_is_home_screen", fake_home_false)
    assert await runner._verify_step_screen(_FakeAdb(), "upgrade_check") is False
    assert await runner._verify_step_screen(_FakeAdb(), "upgrade_execute") is False


@pytest.mark.asyncio
async def test_search_and_attack_pass_without_screen_check(runner, monkeypatch):
    calls = []

    async def fake_attack_screen(adb):
        calls.append("attack")
        return False

    monkeypatch.setattr(runner, "_is_attack_screen", fake_attack_screen)
    assert await runner._verify_step_screen(_FakeAdb(), "search") is True
    assert await runner._verify_step_screen(_FakeAdb(), "attack") is True
    assert calls == []
