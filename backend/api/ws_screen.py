"""WebSocket endpoint for streaming emulator screen as binary PNG frames."""

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.adb.manager import adb_manager
from backend.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/screen")
async def screen_stream(websocket: WebSocket):
    """Stream emulator screen frames as binary PNG via WebSocket.

    The client sends text messages to control streaming:
      - "start" : begin streaming frames
      - "pause" : pause streaming (connection stays open)
      - "stop"  : stop and close

    Frames are sent as binary WebSocket messages (raw PNG bytes).
    Throttled to screencap_fps.
    """
    await websocket.accept()
    logger.info("Screen stream client connected")

    streaming = False
    frame_interval = 1.0 / settings.screencap_fps

    try:
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=0.1)
                if msg == "start":
                    streaming = True
                    logger.info("Screen stream: start")
                elif msg == "pause":
                    streaming = False
                    logger.info("Screen stream: pause")
                elif msg == "stop":
                    logger.info("Screen stream: stop requested")
                    break
            except asyncio.TimeoutError:
                pass

            if streaming and adb_manager.is_connected:
                frame = await adb_manager.screencap()
                if frame is not None:
                    await websocket.send_bytes(frame)
                else:
                    await websocket.send_json({"error": "screencap_failed"})

            await asyncio.sleep(frame_interval)

    except WebSocketDisconnect:
        logger.info("Screen stream client disconnected")
    except Exception as e:
        logger.error("Screen stream error: %s", e)
    finally:
        logger.info("Screen stream ended")
