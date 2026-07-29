"""FastAPI application entry point for CoC-AutoWeb."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.adb.manager import adb_manager

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: nothing to initialize. Shutdown: disconnect ADB."""
    logger.info("CoC-AutoWeb server starting on %s:%d", settings.host, settings.port)
    yield
    logger.info("Shutting down, disconnecting ADB...")
    await adb_manager.disconnect()


app = FastAPI(
    title="CoC-AutoWeb",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/v1/health")
async def health_check():
    """Basic health check endpoint."""
    return {"status": "ok", "adb_connected": adb_manager.is_connected}
