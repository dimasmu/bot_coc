"""FastAPI application entry point for CoC-AutoWeb."""

import logging
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

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

from backend.api.ws_screen import router as screen_router
from backend.api.rest_adb import router as adb_router

app.include_router(screen_router)
app.include_router(adb_router)


@app.get("/api/v1/health")
async def health_check():
    """Basic health check endpoint."""
    return {"status": "ok", "adb_connected": adb_manager.is_connected}


# In production, serve the built frontend
FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"

if FRONTEND_DIST.joinpath("assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    """Serve SPA: return index.html for all unmatched routes."""
    index_path = FRONTEND_DIST / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"error": "Frontend not built"}


@app.get("/")
async def serve_root():
    """Serve the root index.html page."""
    index_path = FRONTEND_DIST / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"error": "Frontend not built"}
