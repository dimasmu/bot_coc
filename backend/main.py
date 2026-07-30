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
from backend.db.database import init_db
from backend.api.ws_status import router as status_router
from backend.engine.fsm import fsm_controller

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize database. Shutdown: disconnect ADB."""
    logger.info("CoC-AutoWeb server starting on %s:%d", settings.host, settings.port)
    logger.info("Initializing database...")
    init_db()
    logger.info("Database ready")
    yield
    logger.info("Shutting down, stopping FSM...")
    if fsm_controller.is_running:
        await fsm_controller.stop()
    logger.info("Disconnecting ADB...")
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
from backend.api.rest_roi import router as roi_router
from backend.api.rest_config import router as config_router
from backend.api.rest_analytics import router as analytics_router
from backend.api.ws_logs import router as logs_router

app.include_router(screen_router)
app.include_router(adb_router)
app.include_router(roi_router)
app.include_router(status_router)
app.include_router(config_router)
app.include_router(analytics_router)
app.include_router(logs_router)


@app.get("/api/v1/health")
async def health_check():
    """Basic health check endpoint."""
    return {"status": "ok", "adb_connected": adb_manager.is_connected}


@app.post("/api/v1/bot/start")
async def bot_start():
    """Start the FSM bot loop."""
    if not fsm_controller.is_running:
        await fsm_controller.start()
    return fsm_controller.get_status_dict()


@app.post("/api/v1/bot/stop")
async def bot_stop():
    """Stop the FSM bot loop."""
    if fsm_controller.is_running:
        await fsm_controller.stop()
    return fsm_controller.get_status_dict()


@app.get("/api/v1/bot/status")
async def bot_status():
    """Get current FSM state and stats."""
    return fsm_controller.get_status_dict()


@app.get("/api/v1/system/backup")
async def system_backup():
    """Download a zip backup of database and templates."""
    import io
    import zipfile
    from pathlib import Path
    from fastapi.responses import StreamingResponse

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        db_path = Path(settings.db_path)
        if db_path.exists():
            zf.write(db_path, "coc_bot.db")
        template_dir = Path(settings.template_dir)
        if template_dir.exists():
            for f in template_dir.glob("*.png"):
                zf.write(f, f"templates/{f.name}")

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=coc-backup.zip"},
    )


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
