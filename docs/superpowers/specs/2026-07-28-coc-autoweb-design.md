# Design Document: CoC-AutoWeb Bot Suite

| Parameter | Detail |
|---|---|
| **Project** | CoC-AutoWeb Bot Suite |
| **Date** | 2026-07-28 |
| **Status** | Approved Design |
| **Source Docs** | CoC_AutoWeb_PRD.md v1.0.0, CoC_AutoWeb_DRD.md v1.0.0 |

---

## 1. Summary

CoC-AutoWeb is a browser-based Clash of Clans automation platform. It uses computer vision (OpenCV + Tesseract OCR) and ADB to control an Android emulator (BlueStacks 5, with LDPlayer as fallback). The user interacts through a web dashboard served by a FastAPI backend. The core bot logic is driven by a Finite State Machine (FSM) with built-in error recovery and humanization to minimize detection risk.

---

## 2. Tech Stack (Final)

| Component | Technology | Notes |
|---|---|---|
| Package Manager | **uv** (astral-sh/uv) | Replaces pip/poetry. Rust-based, 10-100x faster. |
| Python | 3.12+ | `uv init` with `requires-python = ">=3.12"` |
| Backend | FastAPI + Asyncio | WebSocket, SSE streaming (v0.135+), REST endpoints |
| ADB Interface | **adb_shell** (jefflirion/adb_shell) | More actively maintained than pure-python-adb. TCP + USB. |
| Computer Vision | OpenCV 5.x + PyTesseract | Template matching, OCR for loot/trophy reading |
| Database | SQLModel + SQLite | Same author as FastAPI. Zero-config, ACID. |
| ORM | SQLAlchemy 2.0 (via SQLModel) | Async-first via aiosqlite |
| Frontend CSS | Tailwind CSS v4 + Vite | CSS-first config (no tailwind.config.js). Zero-runtime. |
| Frontend JS | Alpine.js (~15KB) | Declarative reactivity for tab state, forms, modals. No build complexity. |
| Charts | Chart.js | Loot rate, search histogram, session history. |
| Validation | Pydantic v2 | Built into FastAPI + SQLModel. |

**Upgrades from PRD spec:**
- `pure-python-adb` → `adb_shell`
- Unspecified Python version → 3.12+
- Unspecified package manager → `uv`
- OpenCV unspecified → 5.x
- TailwindCSS unspecified → Tailwind CSS v4 + Vite
- Added Alpine.js for reactive UI layer

---

## 3. Project Structure

```
CoC-AutoWeb/
├── pyproject.toml
├── .python-version             # 3.12
├── .gitignore
├── backend/
│   ├── __init__.py
│   ├── main.py                 # FastAPI app entry, lifespan, mount static
│   ├── config.py               # Pydantic BaseSettings
│   ├── db/
│   │   ├── __init__.py
│   │   ├── database.py         # SQLite engine, session factory (async)
│   │   └── models.py           # SQLModel tables per DRD schema
│   ├── adb/
│   │   ├── __init__.py
│   │   ├── manager.py          # Connection lifecycle, health checks
│   │   ├── emulators.py        # BlueStacks5/4, LDPlayer adapters
│   │   └── screencap.py        # screencap → PNG bytes
│   ├── vision/
│   │   ├── __init__.py
│   │   ├── matching.py         # OpenCV template matching
│   │   └── ocr.py              # PyTesseract loot/trophy reader
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── fsm.py              # State machine controller + watchdog
│   │   └── states.py           # Per-state handler functions
│   ├── humanize/
│   │   └── __init__.py         # Gaussian offset, dynamic delays, Bezier swipe
│   └── api/
│       ├── __init__.py
│       ├── ws_screen.py        # WebSocket: binary screen streaming
│       ├── ws_logs.py          # WebSocket: structured log streaming
│       ├── ws_status.py        # WebSocket: FSM status + control commands
│       ├── rest_config.py      # REST: config KV CRUD
│       ├── rest_roi.py         # REST: ROI template management
│       └── rest_analytics.py   # REST: attack stats queries
├── frontend/
│   ├── index.html              # SPA entry, Alpine.js root
│   ├── package.json
│   ├── vite.config.js          # Tailwind v4 Vite plugin
│   ├── src/
│   │   ├── main.js             # App init, Alpine store, WS connection manager
│   │   ├── style.css           # Tailwind v4 @import directives
│   │   ├── tabs/
│   │   │   ├── dashboard.js    # Tab 1: Live feed + controls
│   │   │   ├── calibrator.js   # Tab 2: Canvas ROI tool
│   │   │   ├── farming.js      # Tab 3: Strategy config
│   │   │   ├── builder.js      # Tab 4: Upgrade queue
│   │   │   ├── analytics.js    # Tab 5: Charts
│   │   │   └── logs.js         # Tab 6: Terminal
│   │   └── lib/
│   │       ├── canvas-render.js   # Binary frame → canvas decoder
│   │       └── roi-tool.js        # Click-drag bounding box logic
│   └── public/
├── storage/
│   ├── coc_bot.db              # SQLite database (auto-created)
│   └── templates/              # Saved OpenCV template images (.png)
└── tests/
    ├── backend/
    │   ├── test_fsm.py
    │   ├── test_vision.py      # With mock ADB + known screenshots
    │   └── test_humanize.py
    └── frontend/
```

---

## 4. Architecture & Data Flow

### 4.1 Three WebSocket Channels

The frontend and backend communicate over three dedicated WebSocket connections, each with a distinct purpose and data format:

| Channel | Route | Direction | Format | Frequency | Purpose |
|---|---|---|---|---|---|
| Screen Stream | `/ws/screen` | Server → Client | Binary (PNG) | 10-15 FPS | Live emulator view rendered on `<canvas>` |
| Bot Status | `/ws/status` | Bidirectional | JSON | On change / 2s poll | FSM state, loot totals, raid count. Client sends START/PAUSE/STOP. |
| Log Stream | `/ws/logs` | Server → Client | JSON lines | Real-time | Structured log entries with severity, timestamp, message. |

### 4.2 Frontend Serving Strategy

**Development:** Vite dev server on `:5173` with proxy config forwarding `/ws` and `/api` to FastAPI on `:8000`. Hot module replacement for instant CSS/JS updates.

**Production:** `vite build` outputs to `frontend/dist/`. FastAPI mounts this directory as static files at `/` and serves `index.html` for all unmatched routes (SPA fallback).

### 4.3 Screen Streaming Pipeline

```
BlueStacks 5 (CoC screen)
  │  adb exec-out screencap -p
  ▼
PNG bytes (1280x720)
  │  no re-encode, pass through
  ▼
FastAPI WebSocket → send_bytes(frame)
  │  frame throttling: skip if consumer backlog > 2
  ▼
Browser WebSocket → blob → createImageBitmap → ctx.drawImage()
  │  rendered on <canvas> at ~10-15 FPS
  ▼
<canvas> element on Dashboard Tab 1
```

### 4.3 Frontend Component Tree

```
index.html
└── x-data="app" (Alpine.js store)
    ├── TabNavigation         (tab switching via x-show)
    ├── ConnectionBar         (ADB status dot, IP:port input)
    │
    ├── Tab1-Dashboard        (x-show="activeTab === 'dashboard'")
    │   ├── LiveCanvas        (WebSocket binary → canvas render)
    │   ├── StatusIndicator   (FSM state badge)
    │   ├── ControlButtons    (START / PAUSE / EMERGENCY STOP)
    │   └── SummaryCards      (Gold, Elixir, DE, Raids)
    │
    ├── Tab2-Calibrator       (x-show="activeTab === 'calibrator'")
    │   ├── ScreenshotCanvas  (static snapshot for drag)
    │   ├── RoiTool           (click-drag rectangle overlay)
    │   ├── RoiList           (saved templates table)
    │   └── OcrPreview        (test-read selected area)
    │
    ├── Tab3-Farming          (x-show="activeTab === 'farming'")
    │   ├── ThresholdInputs   (min gold/elixir/DE sliders)
    │   ├── TroopPreset       (dropdown selector)
    │   └── StrategySelect    (radio group)
    │
    ├── Tab4-Builder          (x-show="activeTab === 'builder'")
    │   ├── BuilderStatus     (busy builders + timers)
    │   └── UpgradeQueue      (sortable priority list)
    │
    ├── Tab5-Analytics        (x-show="activeTab === 'analytics'")
    │   ├── LootRateChart     (Chart.js line chart)
    │   ├── SearchHistogram   (Chart.js bar chart)
    │   └── HistoryTable      (scrollable table)
    │
    └── Tab6-Logs             (x-show="activeTab === 'logs'")
        ├── SeverityFilter    (INFO/WARN/ERROR/DEBUG toggles)
        └── LogTerminal       (scrolling log lines)
```

### 4.4 ADB Emulator Abstraction

Multi-emulator support via strategy pattern. Each emulator has a dedicated adapter implementing a common interface:

```python
class EmulatorAdapter(ABC):
    adb_host: str
    adb_port: int
    name: str

    @abstractmethod
    def detect_running() -> bool
    @abstractmethod
    def get_device_serial() -> str

# Detection order: BlueStacks 5 → BlueStacks 4 → LDPlayer → Generic TCP
class BlueStacks5Adapter(EmulatorAdapter): ...
class BlueStacks4Adapter(EmulatorAdapter): ...
class LDPlayerAdapter(EmulatorAdapter):    ...
class GenericTcpAdapter(EmulatorAdapter):  ...
```

The `AdbManager` tries each adapter in priority order on startup. Auto-detection checks running processes (BlueStacks: `HD-Player.exe`, LDPlayer: `dnplayer.exe`) and connects to the emulator's default ADB port. If none auto-detect, the user can manually enter IP:port via the dashboard ConnectionBar.

### 4.5 Mock ADB Adapter for Testing

A `MockAdbAdapter` replays pre-captured PNG screenshots from disk. This enables unit testing the FSM and CV pipeline without a running emulator. Screenshots are organized by game state:

```
tests/fixtures/screenshots/
├── main_base.png
├── searching.png
├── attack_preview.png
├── attack_ongoing.png
├── post_battle.png
├── training.png
└── out_of_sync_dialog.png
```

---

## 5. FSM Engine Design

### 5.1 State Definitions

| State | Entry Action | Active Behavior | Exit Conditions |
|---|---|---|---|
| `INIT` | Connect ADB, verify CoC is open, enforce 1280x720 | Wait for stable screen | Done → MAIN_BASE; Fail → Level 3 Recovery (3x retry) → DEAD |
| `MAIN_BASE` | Collect collectors, dismiss popups/donations | Check builder availability, check army readiness | Army ready → SEARCHING; Army not ready → TRAINING; Builder free + upgrade queued → UPGRADING |
| `TRAINING` | Open barracks, tap Quick Train preset | Wait for training timer | Army full → SEARCHING; Timeout (40 min) → MAIN_BASE (re-check) |
| `SEARCHING` | Tap Find Match, read loot via OCR | Tap Next if loot < threshold | Loot ≥ threshold → ATTACKING; Timeout (5 min) → MAIN_BASE |
| `ATTACKING` | Execute deployment script (Bezier swipe) | Monitor battle progress | Battle ends → RETURN_HOME; Timeout (4 min) → Level 3 Recovery |
| `RETURN_HOME` | Wait for post-battle screen, log results | Navigate to home village | Home village detected → MAIN_BASE |
| `UPGRADING` | Select building from priority queue, confirm | Monitor upgrade timer | Upgrade started → MAIN_BASE |
| `DEAD` | (terminal state) | None — bot stopped | Manual restart via dashboard only |

### 5.2 Recovery Sub-Machine

Any state can transition to RECOVERY on unexpected conditions (game crash, out-of-sync dialog, wrong screen, ADB disconnect).

Recovery sequence: `RESTART_GAME → WAIT_LOAD → DETECT_MAIN → RESUME_STATE`

### 5.3 Watchdog Timer Per State

| State | Timeout | Expiry Action |
|---|---|---|
| INIT | 30s | Restart ADB, force-kill CoC |
| SEARCHING | 5 min | Return to MAIN_BASE (clouding/connection) |
| ATTACKING | 4 min | Level 3 full recovery |
| TRAINING | 40 min | Re-check barracks |
| UPGRADING | 60 min | Re-check builder status |

---

## 6. Error Handling

### 6.1 Error Classification

| Level | Name | Condition | Response |
|---|---|---|---|
| L1 | Transient | OCR misread, template not found, brief lag | Retry 3x with progressive backoff (1s, 2s, 4s). Fail → L2 |
| L2 | State Confusion | Expected button not found, on wrong screen | Multi-template scan to identify actual screen. Navigate back to expected state. |
| L3 | Critical | Game crash, "out of sync" dialog, ADB disconnect | Force-stop CoC → restart → wait for main base → resume FSM from INIT. Max 3 consecutive L3 attempts. |
| L4 | Fatal | 3 consecutive L3 failures, emulator won't start | Stop bot, set FSM to DEAD, show red indicator on dashboard. Manual intervention required. |

### 6.2 Screen Landmark Detection

When in doubt about current screen, the bot runs template matching against known landmark elements (stored in `roi_templates` table with `roi_name` prefixed as `landmark_*`). Detection runs in priority order and returns the first screen with a template match confidence > 0.85:
1. Main base: collector icons, attack button, clan castle
2. Attack screen: troop icons, surrender button
3. Post-battle: results text, "Return Home" button
4. Out-of-sync dialog: "Client and server out of sync" text
5. Home screen: settings gear, shop icon

---

## 7. Humanization (Anti-Detection)

| Technique | Implementation |
|---|---|
| Gaussian click offset | Random offset ±5-15px from center within button bounding box |
| Dynamic delays | Random interval between actions, weighted toward human-like timing (e.g., Next: 1.2-3.5s) |
| Bezier swipe path | Control point randomization on troop deployment swipes (avoid straight lines) |
| Break intervals | 10-20 min idle after every 2 hours of active botting |
| Decision variability | Occasional (5-10%) random Next skips on acceptable bases to vary behavior |

All humanization parameters are stored in the `configs` table under `HUMANIZATION` category and adjustable from the dashboard.

---

## 8. Testing Strategy

### 8.1 Unit Tests (no emulator needed)
- FSM state transitions: inject known screenshots via MockAdbAdapter, assert correct state transitions
- OCR accuracy: known-label screenshots, assert correct number reading
- Template matching: known images, assert match confidence > threshold
- Humanization math: assert Gaussian offsets stay within bounds, Bezier paths contain curve points
- API endpoints: FastAPI TestClient with in-memory SQLite

### 8.2 Integration Tests (emulator needed)
- ADB connection + screen capture pipeline: real BlueStacks instance
- Full FSM cycle on a test village: INIT → MAIN_BASE → SEARCHING (Next with zero-loot threshold) → ATTACKING (deploy and wait) → RETURN_HOME

### 8.3 Manual Tests
- Canvas calibrator: drag ROI on live screenshot, save, verify coordinates in DB
- Dashboard WebSocket latency: verify screen feed feels responsive (<150ms)
- Long-running session: run bot for 2+ hours on test account, verify no errors, check humanization breaks

---

## 9. Incremental Implementation Plan

### Phase 1: Live Screen Feed Pipeline (MVP Backbone)
**Goal:** See emulator screen in the dashboard and control ADB connection.

- `uv init` project scaffold with `pyproject.toml`
- FastAPI server with lifespan-managed ADB manager
- WebSocket endpoint `/ws/screen` streaming binary PNG frames
- Vite + Tailwind v4 + Alpine.js frontend shell
- Tab navigation (6 tabs, only Tab 1 active)
- Tab 1: Live `<canvas>` rendering frames from WebSocket
- ConnectionBar: ADB status indicator + manual IP:port input
- BlueStacks5Adapter with auto-detection

### Phase 2: ROI Canvas Calibrator
**Goal:** Define button positions on the live screen, save templates.

- Screenshot capture on-demand (not stream)
- Click-drag bounding box overlay (RoiTool)
- Coordinate CRUD via REST API → SQLite `roi_templates` table
- OCR preview: crop selected area, run PyTesseract, show result

### Phase 3: FSM Engine
**Goal:** Bot can navigate the game autonomously in a controlled loop.

- State machine implementation with all states
- Template matching integration (use saved ROIs)
- OCR integration for loot reading
- Recovery sub-machine
- Watchdog timers
- MockAdbAdapter for unit testing

### Phase 4: Auto-Farm Loop
**Goal:** Full unsupervised farming: Train → Search → Attack → Repeat.

- Wire FSM to real ADB (replace mock)
- Deployment strategies (4-finger, perimeter, snipe)
- Humanization layer (Gaussian tap, Bezier swipe, delays)
- Attack results logging to `attack_logs` table
- Farming config from dashboard (Tab 3)

### Phase 5: Remaining Tabs + Polish
**Goal:** Complete dashboard with all 6 tabs functional.

- Tab 4: Builder status + upgrade queue
- Tab 5: Analytics charts (Chart.js from `attack_logs`)
- Tab 6: Real-time log streaming with severity filter
- Backup endpoint, log retention cleanup
- Break interval scheduler

---

## 10. Key Design Decisions

1. **WebSocket over REST for streaming:** Screen frames push at 10-15 FPS. HTTP polling would be wasteful. Binary WebSocket frames carry raw PNG bytes with no re-encoding overhead.

2. **Three WebSocket channels, not one multiplexed:** Separate connections for screen (high-throughput binary), status (low-frequency JSON), and logs (text). Simplifies client code — each channel has one consumer with one responsibility.

3. **Alpine.js over vanilla JS:** The dashboard has significant reactive state (tab switching, FSM status updates, form sync, connection state). Alpine.js provides this declaratively at ~15KB with zero build configuration. Canvas calibration logic remains vanilla JS — Alpine doesn't own that rendering path.

4. **adb_shell over pure-python-adb:** More active maintenance, better documentation, same API surface. The PRD's original choice was reasonable at the time but this is a direct upgrade.

5. **Mock ADB adapter for testing:** Decouples the FSM and CV pipeline from a physical emulator. Enables CI testing and faster iteration during development.

6. **Strategy pattern for emulator detection:** Each emulator type encapsulates its own detection logic. Adding a new emulator requires only a new adapter class — no changes to the AdbManager.

---

## 11. Non-Functional Requirements

| Requirement | Approach |
|---|---|
| CPU < 15% | Throttle screen capture to 10-15 FPS; skip frames when WebSocket backlog > 2 |
| Latency < 150ms | Direct PNG pass-through (no re-encode); binary WebSocket frames |
| Resolution enforced | ADB `wm size 1280x720` and `wm density 240` on INIT |
| Auto-migration | SQLModel `create_all()` on startup |
| Database < 20MB | 90-day log retention policy, manual cleanup trigger |
| Backup | GET `/api/v1/system/backup` returns `.zip` of `coc_bot.db` + `/storage/templates/` |
