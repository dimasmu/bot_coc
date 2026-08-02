"""WebSocket endpoint for real-time log streaming."""

import asyncio
import json
import logging
import queue
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
router = APIRouter()


class LogHandler(logging.Handler):
    """Captures log records into a queue for WebSocket broadcasting."""

    def __init__(self, max_size: int = 500):
        super().__init__()
        self.queue: queue.Queue[dict] = queue.Queue(maxsize=max_size)

    def emit(self, record: logging.LogRecord):
        from datetime import datetime
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        entry = {
            "ts": ts,
            "level": record.levelname,
            "name": record.name,
            "msg": self.format(record),
        }
        try:
            self.queue.put_nowait(entry)
        except queue.Full:
            # Drop oldest
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(entry)
            except queue.Empty:
                pass


log_handler = LogHandler()
log_handler.setFormatter(logging.Formatter("%(message)s"))

# Attach to root logger
root_logger = logging.getLogger()
root_logger.addHandler(log_handler)


@router.websocket("/ws/logs")
async def log_stream(websocket: WebSocket):
    """Stream log entries as JSON, with client-set severity filter."""
    await websocket.accept()
    logger.info("Log stream client connected")

    filter_level = "ALL"
    level_map = {"ALL": -1, "DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}

    try:
        while True:
            # Check for filter commands
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=0.1)
                data = json.loads(msg)
                if "filter" in data:
                    filter_level = data["filter"].upper()
            except asyncio.TimeoutError:
                pass

            min_lvl = level_map.get(filter_level, 1)
            while not log_handler.queue.empty():
                entry = log_handler.queue.get_nowait()
                entry_lvl = level_map.get(entry["level"], 1)
                if entry_lvl >= min_lvl:
                    await websocket.send_json(entry)

            await asyncio.sleep(0.2)

    except WebSocketDisconnect:
        logger.info("Log stream client disconnected")
    except Exception as e:
        logger.error("Log stream error: %s", e)
