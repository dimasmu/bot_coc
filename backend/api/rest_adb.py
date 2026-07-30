"""REST endpoints for ADB connection management."""

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from backend.adb.manager import adb_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/adb")


class AdbConnectRequest(BaseModel):
    host: str = "127.0.0.1"
    port: int = 5555


class AdbConnectResponse(BaseModel):
    success: bool
    status: dict


@router.post("/connect")
async def connect_adb(req: AdbConnectRequest) -> AdbConnectResponse:
    """Connect to an emulator via ADB."""
    success = await adb_manager.connect(host=req.host, port=req.port)
    return AdbConnectResponse(
        success=success,
        status={
            "connected": adb_manager.status.connected,
            "emulatorName": adb_manager.status.emulator_name,
            "serial": adb_manager.status.serial,
            "screenSize": adb_manager.status.screen_size,
        },
    )


@router.post("/disconnect")
async def disconnect_adb():
    """Disconnect from the current ADB device."""
    await adb_manager.disconnect()
    return {"success": True}


@router.get("/status")
async def get_adb_status():
    """Get current ADB connection status."""
    return {
        "connected": adb_manager.status.connected,
        "emulatorName": adb_manager.status.emulator_name,
        "serial": adb_manager.status.serial,
        "screenSize": adb_manager.status.screen_size,
    }


@router.post("/auto-connect")
async def auto_connect() -> AdbConnectResponse:
    """Auto-detect and connect to a running emulator."""
    success = await adb_manager.connect()
    return AdbConnectResponse(
        success=success,
        status={
            "connected": adb_manager.status.connected,
            "emulatorName": adb_manager.status.emulator_name,
            "serial": adb_manager.status.serial,
            "screenSize": adb_manager.status.screen_size,
        },
    )
