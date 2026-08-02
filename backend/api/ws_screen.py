"""WebSocket endpoint for streaming emulator screen as binary frames."""

import asyncio
import io
import json
import logging

from PIL import Image
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.adb.manager import adb_manager
from backend.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

SCALE = 2  # downscale factor (1280x720 → 640x360)


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
                elif msg.startswith("tap "):
                    # Incoming: "tap <x> <y>" from canvas click (scaled coords)
                    try:
                        parts = msg.split()
                        tap_x = int(float(parts[1]) * SCALE)
                        tap_y = int(float(parts[2]) * SCALE)
                        await adb_manager.tap(tap_x, tap_y)
                        logger.debug("Canvas tap at (%d,%d) → screen (%d,%d)",
                                     parts[1], parts[2], tap_x, tap_y)
                    except (ValueError, IndexError):
                        pass
            except asyncio.TimeoutError:
                pass

            if streaming and adb_manager.is_connected:
                frame = await adb_manager.screencap()
                if frame is not None:
                    # Resize for faster streaming (1280x720 → 640x360)
                    img = Image.open(io.BytesIO(frame))
                    img = img.resize(
                        (img.width // SCALE, img.height // SCALE),
                        Image.LANCZOS)
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=50)
                    await websocket.send_bytes(buf.getvalue())
                else:
                    await websocket.send_json({"error": "screencap_failed"})

            await asyncio.sleep(frame_interval)

    except WebSocketDisconnect:
        logger.info("Screen stream client disconnected")
    except Exception as e:
        logger.error("Screen stream error: %s", e)
    finally:
        logger.info("Screen stream ended")
