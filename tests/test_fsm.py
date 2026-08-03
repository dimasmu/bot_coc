"""Tests for the FSM engine using MockAdbManager."""

import asyncio
import pytest

from backend.adb.mock import MockAdbManager
from backend.engine.fsm import FsmController, BotState


@pytest.fixture
def fsm():
    mock = MockAdbManager()
    return FsmController(adb=mock)


@pytest.mark.asyncio
async def test_fsm_initial_state(fsm):
    assert fsm.state == BotState.STOPPED
    assert not fsm.is_running


@pytest.mark.asyncio
async def test_fsm_start_stop(fsm):
    await fsm.start()
    # Give it a moment to process
    await asyncio.sleep(0.5)
    assert fsm.is_running

    await fsm.stop()
    assert fsm.state == BotState.STOPPED


@pytest.mark.asyncio
async def test_fsm_state_progression(fsm):
    await fsm.start()
    await asyncio.sleep(1)
    # Should have progressed past INIT at minimum
    assert fsm.state != BotState.STOPPED
    await fsm.stop()
