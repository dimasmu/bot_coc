"""WebSocket endpoint for FSM status streaming and control."""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.engine.sequence_runner import sequence_runner

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/status")
async def status_stream(websocket: WebSocket):
    """Bidirectional channel: server pushes FSM status, client sends control commands."""
    await websocket.accept()
    logger.info("Status stream client connected")

    try:
        while True:
            # Check for control messages
            try:
                msg = await asyncio.wait_for(
                    websocket.receive_text(), timeout=2.0
                )
                data = json.loads(msg)
                cmd = data.get("command")

                if cmd == "start":
                    await sequence_runner.start()
                elif cmd == "stop":
                    await sequence_runner.stop()
                elif cmd == "pause":
                    await sequence_runner.stop()
                elif cmd == "read_resources":
                    await sequence_runner.read_current_resources()

            except asyncio.TimeoutError:
                pass

            # Send current status
            await websocket.send_json(sequence_runner.get_status_dict())

    except WebSocketDisconnect:
        logger.info("Status stream client disconnected")
    except Exception as e:
        logger.error("Status stream error: %s", e)
