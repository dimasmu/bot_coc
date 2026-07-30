# CoC-AutoWeb Phase 1: Live Screen Feed Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the MVP backbone: see the BlueStacks 5 emulator screen inside a web dashboard via WebSocket streaming, with ADB connection management and 6-tab navigation shell.

**Architecture:** A FastAPI backend captures BlueStacks 5 screen via adb_shell and streams raw PNG frames over a binary WebSocket. A Vite + Tailwind v4 + Alpine.js frontend renders frames onto a `<canvas>`, with ADB status indicator, manual IP:port input, and a tab navigation shell with 6 empty tabs (only Tab 1 active).

**Tech Stack:** Python 3.12+, uv, FastAPI, adb_shell, Vite, Tailwind CSS v4, Alpine.js

**Source Spec:** `docs/superpowers/specs/2026-07-28-coc-autoweb-design.md`

---

## Task 1: Initialize project scaffold with uv

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `.gitignore`
- Create: `backend/__init__.py`

- [ ] **Step 1: Initialize the project**

Run: `uv init --no-readme --app CoC-AutoWeb`
(skip `--app` if uv version doesn't support it — just `uv init`)
Run from: `C:\programming\python`

Expected: Creates `pyproject.toml` and `hello.py` (delete `hello.py`).

- [ ] **Step 2: Replace pyproject.toml with full project definition**

Write `pyproject.toml`:

```toml
[project]
name = "coc-autoweb"
version = "0.1.0"
description = "Clash of Clans Web-Based Automation Suite"
requires-python = ">=3.12"
dependencies = [
    "fastapi[standard]>=0.115",
    "adb-shell>=0.4",
    "opencv-python-headless>=4.10",
    "pytesseract>=0.3",
    "sqlmodel>=0.0",
    "aiosqlite>=0.20",
    "pydantic-settings>=2.0",
    "pillow>=11.0",
]

[project.scripts]
coc-bot = "backend.main:main"
```

- [ ] **Step 3: Set Python version**

Write `.python-version`:
```
3.12
```

- [ ] **Step 4: Write .gitignore**

Write `.gitignore`:
```
__pycache__/
*.pyc
*.pyo
.env
.venv/
*.egg-info/
dist/
build/
storage/coc_bot.db
storage/templates/*.png
node_modules/
.superpowers/
```

- [ ] **Step 5: Create backend package init**

Write `backend/__init__.py`:
```python
"""CoC-AutoWeb backend package."""
```

- [ ] **Step 6: Install dependencies and verify**

Run: `uv sync`
Expected: Creates `.venv/`, installs all dependencies, generates `uv.lock`.

- [ ] **Step 7: Verify Python version**

Run: `uv run python --version`
Expected: `Python 3.12.x` (or higher)

---

## Task 2: Implement backend configuration

**Files:**
- Create: `backend/config.py`

- [ ] **Step 1: Write config module**

Write `backend/config.py`:

```python
"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "COC_", "env_file": ".env", "extra": "ignore"}

    host: str = "127.0.0.1"
    port: int = 8000
    db_path: str = "storage/coc_bot.db"
    template_dir: str = "storage/templates"
    adb_host: str = "127.0.0.1"
    adb_port: int = 5555
    screen_width: int = 1280
    screen_height: int = 720
    screen_dpi: int = 240
    screencap_fps: int = 15
    log_level: str = "INFO"


settings = Settings()
```

- [ ] **Step 2: Verify module imports**

Run: `uv run python -c "from backend.config import settings; print(settings.host)"`
Expected: `127.0.0.1`

---

## Task 3: Implement ADB emulator adapters

**Files:**
- Create: `backend/adb/__init__.py`
- Create: `backend/adb/emulators.py`

- [ ] **Step 1: Create adb package init**

Write `backend/adb/__init__.py`:
```python
"""ADB interface layer for Android emulator control."""
```

- [ ] **Step 2: Write emulator adapters**

Write `backend/adb/emulators.py`:

```python
"""Emulator-specific ADB adapters with auto-detection."""

from abc import ABC, abstractmethod
import subprocess
import sys


class EmulatorAdapter(ABC):
    """Base class for emulator ADB connections."""

    adb_host: str = "127.0.0.1"
    adb_port: int = 5555
    name: str = "generic"

    @abstractmethod
    def detect_running(self) -> bool:
        """Return True if this emulator is running."""
        ...

    def get_device_serial(self) -> str:
        """Return the ADB device serial string."""
        return f"{self.adb_host}:{self.adb_port}"


class BlueStacks5Adapter(EmulatorAdapter):
    """BlueStacks 5 running on default port 5555."""

    adb_port = 5555
    name = "BlueStacks 5"

    def detect_running(self) -> bool:
        if sys.platform == "win32":
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq HD-Player.exe"],
                    capture_output=True,
                    text=True,
                )
                return "HD-Player.exe" in result.stdout
            except FileNotFoundError:
                return False
        return False


class BlueStacks4Adapter(EmulatorAdapter):
    """BlueStacks 4 — same process name, different config layout."""

    adb_port = 5555
    name = "BlueStacks 4"

    def detect_running(self) -> bool:
        # Shares process name with BS5; differentiate in manager via config path
        if sys.platform == "win32":
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq HD-Player.exe"],
                    capture_output=True,
                    text=True,
                )
                return "HD-Player.exe" in result.stdout
            except FileNotFoundError:
                return False
        return False


class LDPlayerAdapter(EmulatorAdapter):
    """LDPlayer emulator."""

    adb_port = 5555
    name = "LDPlayer"

    def detect_running(self) -> bool:
        if sys.platform == "win32":
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq dnplayer.exe"],
                    capture_output=True,
                    text=True,
                )
                return "dnplayer.exe" in result.stdout
            except FileNotFoundError:
                return False
        return False


class GenericTcpAdapter(EmulatorAdapter):
    """Fallback: user-provided IP:port."""

    name = "Generic TCP"

    def detect_running(self) -> bool:
        # Cannot detect — user must provide IP:port manually
        return False


def get_emulator_adapters(
    host_override: str | None = None,
    port_override: int | None = None,
) -> list[EmulatorAdapter]:
    """Return adapters in detection priority order.

    If host/port overrides are given, the GenericTcpAdapter uses them
    and is moved to first position.
    """
    adapters: list[EmulatorAdapter] = [
        BlueStacks5Adapter(),
        BlueStacks4Adapter(),
        LDPlayerAdapter(),
        GenericTcpAdapter(),
    ]

    if host_override is not None:
        generic = adapters[-1]
        generic.adb_host = host_override
        if port_override is not None:
            generic.adb_port = port_override
        # Move generic to front so it's tried first on manual connect
        adapters.insert(0, adapters.pop())

    return adapters
```

- [ ] **Step 3: Verify adapters import**

Run: `uv run python -c "from backend.adb.emulators import BlueStacks5Adapter, get_emulator_adapters; print(get_emulator_adapters()[0].name)"`
Expected: `BlueStacks 5`

---

## Task 4: Implement ADB Manager (connection lifecycle + screencap)

**Files:**
- Create: `backend/adb/manager.py`

- [ ] **Step 1: Write ADB Manager**

Write `backend/adb/manager.py`:

```python
"""ADB connection lifecycle manager with health checks and screencap."""

import asyncio
import logging
from dataclasses import dataclass

from adb_shell.adb_device import AdbDeviceTcp
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from adb_shell.auth.keygen import keygen
from pathlib import Path

from backend.adb.emulators import EmulatorAdapter, get_emulator_adapters
from backend.config import settings

logger = logging.getLogger(__name__)


@dataclass
class AdbStatus:
    connected: bool = False
    emulator_name: str = ""
    serial: str = ""
    screen_size: str = ""


class AdbManager:
    """Manages the lifecycle of an ADB connection to an Android emulator."""

    def __init__(self):
        self._device: AdbDeviceTcp | None = None
        self._signer: PythonRSASigner | None = None
        self._adapter: EmulatorAdapter | None = None
        self.status = AdbStatus()
        self._lock = asyncio.Lock()

    def _ensure_keys(self, key_path: str = "storage/adbkey") -> PythonRSASigner:
        """Load or generate ADB RSA key pair."""
        key_file = Path(key_path)
        pub_file = Path(str(key_path) + ".pub")
        if not key_file.exists() or not pub_file.exists():
            key_file.parent.mkdir(parents=True, exist_ok=True)
            keygen(str(key_file))
            logger.info("Generated new ADB keypair at %s", key_file)

        with open(key_file) as f:
            priv = f.read()
        with open(pub_file) as f:
            pub = f.read()
        return PythonRSASigner(pub, priv)

    async def connect(
        self,
        host: str | None = None,
        port: int | None = None,
    ) -> bool:
        """Connect to an emulator. Tries auto-detection, falls back to manual.

        Returns True if connected successfully.
        """
        async with self._lock:
            # Disconnect existing
            await self.disconnect()

            self._signer = self._ensure_keys()

            adapters = get_emulator_adapters(
                host_override=host,
                port_override=port,
            )

            for adapter in adapters:
                if host is None and not adapter.detect_running():
                    logger.debug("Skipping %s: not detected", adapter.name)
                    continue

                logger.info("Trying %s at %s:%d ...", adapter.name, adapter.adb_host, adapter.adb_port)
                try:
                    device = AdbDeviceTcp(
                        adapter.adb_host,
                        adapter.adb_port,
                        default_transport_timeout_s=9.0,
                    )
                    device.connect(rsa_keys=[self._signer], auth_timeout_s=0.1)
                    self._device = device
                    self._adapter = adapter

                    # Verify: get screen size
                    resolution = device.shell("wm size")
                    self.status = AdbStatus(
                        connected=True,
                        emulator_name=adapter.name,
                        serial=adapter.get_device_serial(),
                        screen_size=resolution.strip(),
                    )
                    logger.info("Connected to %s (%s)", adapter.name, resolution.strip())
                    return True

                except Exception as e:
                    logger.warning("Failed to connect to %s: %s", adapter.name, e)
                    self._device = None

            self.status = AdbStatus()
            return False

    async def disconnect(self) -> None:
        """Close the ADB connection."""
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass
            self._device = None
            self._adapter = None
            self.status = AdbStatus()

    async def screencap(self) -> bytes | None:
        """Capture the device screen as PNG bytes.

        Returns None if the capture fails.
        """
        if self._device is None:
            return None

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                None,
                lambda: self._device.shell("screencap -p", decode=False),
            )
        except Exception as e:
            logger.error("screencap failed: %s", e)
            return None

    async def set_resolution(self) -> bool:
        """Enforce target resolution on the device."""
        if self._device is None:
            return False
        try:
            self._device.shell(f"wm size {settings.screen_width}x{settings.screen_height}")
            self._device.shell(f"wm density {settings.screen_dpi}")
            return True
        except Exception as e:
            logger.error("Failed to set resolution: %s", e)
            return False

    async def tap(self, x: int, y: int) -> bool:
        """Tap at coordinates."""
        if self._device is None:
            return False
        try:
            self._device.shell(f"input tap {x} {y}")
            return True
        except Exception as e:
            logger.error("tap failed: %s", e)
            return False

    async def health_check(self) -> bool:
        """Check if the ADB connection is still alive."""
        if self._device is None:
            return False
        try:
            self._device.shell("echo ok")
            return True
        except Exception:
            return False

    @property
    def is_connected(self) -> bool:
        return self.status.connected


adb_manager = AdbManager()
```

- [ ] **Step 2: Create storage directory for adb keys**

Run: `mkdir storage`

- [ ] **Step 3: Verify manager module imports**

Run: `uv run python -c "from backend.adb.manager import adb_manager; print(adb_manager.status)"`
Expected: `AdbStatus(connected=False, ...)`

---

## Task 5: Implement FastAPI application with lifespan

**Files:**
- Create: `backend/main.py`

- [ ] **Step 1: Write FastAPI application**

Write `backend/main.py`:

```python
"""FastAPI application entry point for CoC-AutoWeb."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

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
```

- [ ] **Step 2: Verify app starts**

Run: `uv run fastapi dev backend/main.py` (Ctrl+C after it prints "Application startup complete")
Expected: Server starts without errors on port 8000.

---

## Task 6: Implement WebSocket screen streaming endpoint

**Files:**
- Create: `backend/api/__init__.py`
- Create: `backend/api/ws_screen.py`
- Modify: `backend/main.py` (register router)

- [ ] **Step 1: Create api package init**

Write `backend/api/__init__.py`:
```python
"""REST and WebSocket API endpoints."""
```

- [ ] **Step 2: Write screen streaming WebSocket handler**

Write `backend/api/ws_screen.py`:

```python
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
            # Listen for control messages with a timeout
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
                pass  # No message, continue

            if streaming and adb_manager.is_connected:
                frame = await adb_manager.screencap()
                if frame is not None:
                    await websocket.send_bytes(frame)
                else:
                    # Screencap failed — send a JSON error so the client knows
                    await websocket.send_json(
                        {"error": "screencap_failed"}
                    )

            await asyncio.sleep(frame_interval)

    except WebSocketDisconnect:
        logger.info("Screen stream client disconnected")
    except Exception as e:
        logger.error("Screen stream error: %s", e)
    finally:
        logger.info("Screen stream ended")
```

- [ ] **Step 3: Register router in main.py**

In `backend/main.py`, add after the `app = FastAPI(...)` block and before the health endpoint:

```python
from backend.api.ws_screen import router as screen_router

app.include_router(screen_router)
```

- [ ] **Step 4: Verify router imports**

Run: `uv run python -c "from backend.api.ws_screen import router; print(len(router.routes))"`
Expected: `1`

---

## Task 7: Set up frontend with Vite + Tailwind CSS v4 + Alpine.js

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.js`
- Create: `frontend/index.html`
- Create: `frontend/src/style.css`
- Create: `frontend/src/main.js`

- [ ] **Step 1: Write package.json**

Write `frontend/package.json`:

```json
{
  "name": "coc-autoweb-frontend",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "alpinejs": "^3.14"
  },
  "devDependencies": {
    "tailwindcss": "^4.0",
    "@tailwindcss/vite": "^4.0",
    "vite": "^6.0"
  }
}
```

- [ ] **Step 2: Write vite.config.js**

Write `frontend/vite.config.js`:

```js
import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [tailwindcss()],
  server: {
    proxy: {
      "/ws": {
        target: "ws://127.0.0.1:8000",
        ws: true,
      },
      "/api": {
        target: "http://127.0.0.1:8000",
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
```

- [ ] **Step 3: Write style.css (Tailwind v4)**

Write `frontend/src/style.css`:

```css
@import "tailwindcss";

/* Custom scrollbar for log terminal */
.scrollbar-thin::-webkit-scrollbar {
  width: 6px;
}
.scrollbar-thin::-webkit-scrollbar-track {
  background: #1e293b;
}
.scrollbar-thin::-webkit-scrollbar-thumb {
  background: #475569;
  border-radius: 3px;
}

/* Canvas container: maintain aspect ratio for 1280x720 */
.canvas-container {
  aspect-ratio: 16 / 9;
  max-height: calc(100vh - 200px);
}
```

- [ ] **Step 4: Write index.html with Alpine.js root**

Write `frontend/index.html`:

```html
<!DOCTYPE html>
<html lang="en" class="bg-slate-900 text-slate-100">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>CoC-AutoWeb</title>
  <script defer src="/main.js"></script>
  <link rel="icon" type="image/png" href="data:image/png;base64,iVBORw0KGgo=" />
</head>
<body x-data="app" class="min-h-screen">
  <!-- Connection Bar -->
  <div class="flex items-center gap-4 px-4 py-2 bg-slate-800 border-b border-slate-700">
    <div class="flex items-center gap-2">
      <span
        class="inline-block w-3 h-3 rounded-full"
        :class="adbStatus.connected ? 'bg-green-400' : 'bg-red-400'"
      ></span>
      <span class="text-sm font-medium" x-text="adbStatus.connected ? adbStatus.emulatorName : 'Disconnected'"></span>
    </div>
    <div class="flex items-center gap-2 ml-auto">
      <input
        type="text"
        x-model="adbHost"
        placeholder="127.0.0.1:5555"
        class="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm w-40 font-mono"
      />
      <button
        @click="connectAdb()"
        class="bg-blue-600 hover:bg-blue-500 text-white text-sm px-3 py-1 rounded transition-colors"
        :disabled="connecting"
        x-text="connecting ? 'Connecting...' : adbStatus.connected ? 'Reconnect' : 'Connect'"
      ></button>
      <button
        @click="disconnectAdb()"
        class="bg-slate-600 hover:bg-slate-500 text-white text-sm px-3 py-1 rounded transition-colors"
        x-show="adbStatus.connected"
      >Disconnect</button>
    </div>
  </div>

  <!-- Tab Navigation -->
  <nav class="flex gap-1 px-4 bg-slate-850 border-b border-slate-700">
    <template x-for="tab in tabs" :key="tab.id">
      <button
        @click="activeTab = tab.id"
        class="px-4 py-2 text-sm font-medium transition-colors border-b-2"
        :class="activeTab === tab.id ? 'border-blue-500 text-blue-400' : 'border-transparent text-slate-400 hover:text-slate-200'"
        x-text="tab.label"
      ></button>
    </template>
  </nav>

  <!-- Tab Content -->
  <main class="p-4">
    <!-- Tab 1: Dashboard -->
    <div x-show="activeTab === 'dashboard'">
      <div class="flex gap-4 flex-wrap">
        <!-- Live Feed -->
        <div class="flex-1 min-w-[400px]">
          <div class="bg-slate-800 rounded-lg border border-slate-700 overflow-hidden canvas-container">
            <canvas id="screenCanvas" class="w-full h-full object-contain bg-black"></canvas>
          </div>
          <div class="flex gap-2 mt-3">
            <button @click="startStream()" :disabled="!adbStatus.connected || streaming"
              class="bg-green-600 hover:bg-green-500 disabled:opacity-50 disabled:cursor-not-allowed text-white px-4 py-1.5 rounded text-sm transition-colors">
              START
            </button>
            <button @click="pauseStream()" :disabled="!streaming"
              class="bg-yellow-600 hover:bg-yellow-500 disabled:opacity-50 disabled:cursor-not-allowed text-white px-4 py-1.5 rounded text-sm transition-colors">
              PAUSE
            </button>
            <button @click="stopStream()" :disabled="!wsScreen"
              class="bg-red-600 hover:bg-red-500 disabled:opacity-50 disabled:cursor-not-allowed text-white px-4 py-1.5 rounded text-sm transition-colors">
              STOP
            </button>
            <span class="text-xs text-slate-400 self-center ml-2">
              FPS: <span x-text="fps">0</span>
            </span>
          </div>
        </div>

        <!-- Status Panel -->
        <div class="w-64 flex flex-col gap-3">
          <div class="bg-slate-800 rounded-lg border border-slate-700 p-4">
            <h3 class="text-xs uppercase tracking-wider text-slate-400 mb-2">Bot Status</h3>
            <span class="inline-block px-2 py-1 rounded text-xs font-bold bg-slate-700 text-slate-300">IDLE</span>
          </div>
          <div class="bg-slate-800 rounded-lg border border-slate-700 p-4">
            <h3 class="text-xs uppercase tracking-wider text-slate-400 mb-2">Summary</h3>
            <div class="text-sm space-y-1">
              <div class="flex justify-between"><span>Gold</span><span class="text-yellow-400">--</span></div>
              <div class="flex justify-between"><span>Elixir</span><span class="text-pink-400">--</span></div>
              <div class="flex justify-between"><span>Dark E.</span><span class="text-slate-400">--</span></div>
              <div class="flex justify-between"><span>Raids</span><span>0</span></div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Tabs 2-6: Placeholder shells -->
    <template x-for="tab in tabs.filter(t => t.id !== 'dashboard')" :key="tab.id">
      <div x-show="activeTab === tab.id" class="flex items-center justify-center h-64">
        <p class="text-slate-500 text-lg" x-text="tab.label + ' — Coming Soon'"></p>
      </div>
    </template>
  </main>

</body>
</html>
```

- [ ] **Step 5: Write main.js (Alpine.js app + WebSocket screen stream)**

Write `frontend/src/main.js`:

```js
import Alpine from "alpinejs";
import "./style.css";

document.addEventListener("alpine:init", () => {
  Alpine.data("app", () => ({
    // Tab state
    tabs: [
      { id: "dashboard", label: "Dashboard" },
      { id: "calibrator", label: "Calibrator" },
      { id: "farming", label: "Farming" },
      { id: "builder", label: "Builder" },
      { id: "analytics", label: "Analytics" },
      { id: "logs", label: "Logs" },
    ],
    activeTab: "dashboard",

    // ADB connection state
    adbStatus: { connected: false, emulatorName: "", serial: "", screenSize: "" },
    adbHost: "127.0.0.1:5555",
    connecting: false,

    // Stream state
    wsScreen: null,
    streaming: false,
    fps: 0,
    _frameCount: 0,
    _fpsInterval: null,

    init() {
      this.startFpsCounter();
    },

    startFpsCounter() {
      this._fpsInterval = setInterval(() => {
        this.fps = this._frameCount;
        this._frameCount = 0;
      }, 1000);
    },

    async connectAdb() {
      this.connecting = true;
      const [host, portStr] = this.adbHost.split(":");
      const port = parseInt(portStr) || 5555;

      try {
        const res = await fetch("/api/v1/adb/connect", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ host, port }),
        });
        const data = await res.json();
        this.adbStatus = data.status;
      } catch (e) {
        console.error("ADB connect failed:", e);
      } finally {
        this.connecting = false;
      }
    },

    async disconnectAdb() {
      await fetch("/api/v1/adb/disconnect", { method: "POST" });
      this.adbStatus = { connected: false, emulatorName: "", serial: "", screenSize: "" };
    },

    startStream() {
      if (this.wsScreen) return;

      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${proto}//${location.host}/ws/screen`);

      ws.binaryType = "blob";

      ws.onopen = () => {
        ws.send("start");
        this.streaming = true;
      };

      const canvas = document.getElementById("screenCanvas");
      const ctx = canvas.getContext("2d");

      ws.onmessage = async (event) => {
        if (event.data instanceof Blob) {
          this._frameCount++;
          try {
            const bitmap = await createImageBitmap(event.data);
            // Resize canvas to match frame if dimensions differ
            if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
              canvas.width = bitmap.width;
              canvas.height = bitmap.height;
            }
            ctx.drawImage(bitmap, 0, 0);
            bitmap.close();
          } catch (e) {
            // JSON error message, ignore binary parse failures
          }
        }
      };

      ws.onclose = () => {
        this.streaming = false;
        this.wsScreen = null;
      };

      ws.onerror = () => {
        this.streaming = false;
      };

      this.wsScreen = ws;
    },

    pauseStream() {
      if (this.wsScreen && this.wsScreen.readyState === WebSocket.OPEN) {
        this.wsScreen.send("pause");
        this.streaming = false;
      }
    },

    stopStream() {
      if (this.wsScreen) {
        if (this.wsScreen.readyState === WebSocket.OPEN) {
          this.wsScreen.send("stop");
        }
        this.wsScreen.close();
        this.wsScreen = null;
        this.streaming = false;
      }
    },
  }));
});

Alpine.start();
```

- [ ] **Step 6: Install frontend dependencies**

Run: `cd frontend && npm install`
Expected: Installs alpinejs, tailwindcss, @tailwindcss/vite, vite.

- [ ] **Step 7: Verify frontend builds**

Run: `cd frontend && npx vite build`
Expected: Builds to `frontend/dist/` without errors.

- [ ] **Step 8: Verify dev server starts**

Run: `cd frontend && npx vite` (Ctrl+C after confirming it prints a local URL)
Expected: Dev server starts on port 5173.

---

## Task 8: Add ADB REST endpoints for connect/disconnect

**Files:**
- Create: `backend/api/rest_adb.py`
- Modify: `backend/main.py` (register router)

- [ ] **Step 1: Write ADB REST endpoints**

Write `backend/api/rest_adb.py`:

```python
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
```

- [ ] **Step 2: Register router in main.py**

In `backend/main.py`, add below the existing router include:

```python
from backend.api.rest_adb import router as adb_router

app.include_router(adb_router)
```

- [ ] **Step 3: Verify endpoints register**

Run: `uv run python -c "from backend.main import app; routes = [r.path for r in app.routes]; print(routes)"`
Expected: Should include `/api/v1/adb/connect`, `/api/v1/adb/disconnect`, etc.

---

## Task 9: Serve frontend from FastAPI in production mode

**Files:**
- Modify: `backend/main.py` (mount static files)

- [ ] **Step 1: Add static file mount to main.py**

In `backend/main.py`, add at the bottom (after all router registrations), and add the import for `os` and `Path`:

```python
import os
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# In production, serve the built frontend
FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
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
        return FileResponse(FRONTEND_DIST / "index.html")
```

- [ ] **Step 2: Build frontend and verify FastAPI serves it**

Run:
```
cd frontend && npx vite build && cd ..
uv run fastapi dev backend/main.py
```

Visit http://127.0.0.1:8000 — expected: dashboard HTML loads. (Ctrl+C to stop.)

---

## Task 10: Verify end-to-end with BlueStacks 5

**Files:**
- None (manual verification)

- [ ] **Step 1: Start BlueStacks 5 with Clash of Clans running**

- Open BlueStacks 5
- Confirm CoC is on the main village screen
- Note: ADB must be enabled in BlueStacks settings (Settings → Advanced → Enable Android Debug Bridge)

- [ ] **Step 2: Start FastAPI backend**

Run: `uv run fastapi dev backend/main.py`

- [ ] **Step 3: Start Vite dev server (separate terminal)**

Run: `cd frontend && npx vite`

- [ ] **Step 4: Open browser at http://localhost:5173**

Expected:
- Tab navigation shows 6 tabs
- ADB status dot is red (Disconnected)
- Click "Connect" (leave IP as 127.0.0.1:5555)
- ADB status dot turns green, shows "BlueStacks 5"
- Click "START" on the screen feed panel
- BlueStacks screen appears on the canvas
- FPS counter shows ~10-15

- [ ] **Step 5: Test controls**

- Click PAUSE → feed freezes, FPS drops to 0
- Click START → feed resumes
- Click STOP → feed stops, canvas freezes
- Click Disconnect → ADB status turns red

- [ ] **Step 6: Test tab switching**

- Click each tab — Dashboard shows live content, others show "Coming Soon"

---

## Task 11: Add a simple startup convenience script

**Files:**
- Create: `run.bat` (Windows)
- Create: `run.sh` (macOS/Linux)

- [ ] **Step 1: Write Windows batch script**

Write `run.bat`:

```batch
@echo off
echo ========================================
echo   CoC-AutoWeb - Starting both servers
echo ========================================
echo.
echo Backend: http://127.0.0.1:8000
echo Frontend dev: http://localhost:5173 (open this)
echo.

start "CoC-Backend" cmd /c "uv run fastapi dev backend/main.py"
timeout /t 3 /nobreak >nul
start "CoC-Frontend" cmd /c "cd frontend && npx vite"

echo Both servers starting...
echo Close this window to stop both servers.
pause
```

- [ ] **Step 2: Write Unix shell script**

Write `run.sh`:

```bash
#!/usr/bin/env bash
set -e

echo "========================================"
echo "  CoC-AutoWeb - Starting both servers"
echo "========================================"
echo ""
echo "Backend: http://127.0.0.1:8000"
echo "Frontend: http://localhost:5173 (open this)"
echo ""

uv run fastapi dev backend/main.py &
BACKEND_PID=$!
sleep 3

cd frontend && npx vite &
FRONTEND_PID=$!

echo "Both servers started. Press Ctrl+C to stop."
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
```

- [ ] **Step 3: Make run.sh executable (Unix only)**

Run: `chmod +x run.sh`

---

## Task 12: Commit

- [ ] **Step 1: Stage and commit all Phase 1 files**

Run:
```
git add -A
git commit -m "feat: Phase 1 - Live screen feed pipeline with ADB streaming dashboard

- uv project scaffold with FastAPI, adb_shell, and frontend deps
- ADB emulator adapters (BlueStacks 5, BlueStacks 4, LDPlayer, Generic TCP)
- AdbManager with connection lifecycle, screencap, and health checks
- WebSocket endpoint /ws/screen for binary PNG frame streaming
- Vite + Tailwind CSS v4 + Alpine.js dashboard with 6-tab navigation
- Live canvas rendering from WebSocket binary frames
- ADB connection bar with status indicator and manual IP:port input"
```
