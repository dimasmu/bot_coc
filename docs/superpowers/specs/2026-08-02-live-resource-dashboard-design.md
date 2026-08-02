# Live Resource Dashboard — Design Spec

**Date**: 2026-08-02
**Status**: Approved
**Goal**: Show current resource balances (gold, elixir, dark elixir, gems) live in the Dashboard Summary panel, updated whenever the bot reads resources during its loop.

## Problem

Dashboard Summary panel at `index.html:106-115` shows hardcoded `--` placeholders for Gold, Elixir, and Dark Elixir. The WebSocket `/ws/status` reports `gold_earned`/`elixir_earned`/`dark_elixir_earned` (intended as cumulative loot stats) but these values are never populated. There is no live view of the player's actual resource balances.

## Design

### Data Flow

```
Bot finishes farming/upgrade loop (back at base)
  → sequence_runner.read_current_resources()
    → adb_manager.screencap()
    → OCR own_gold_number, own_elixir_number, own_dark_elixir_number, own_gems_number
    → store in self.current_gold/elixir/dark_elixir/gems
  → get_status_dict() includes current_* fields
  → WebSocket /ws/status pushes to frontend every ~2s
  → Dashboard Summary renders live values
```

### Trigger points

1. **After ADB connect**: send `{ command: "read_resources" }` via WS status → back to base → read resources
2. **End of each farming/upgrade loop**: after returning to base, call `read_current_resources()`

### Backend Changes

#### `backend/engine/sequence_runner.py`

**New instance variables:**
```python
self.current_gold = 0
self.current_elixir = 0
self.current_dark_elixir = 0
self.current_gems = 0
```

**New method `read_current_resources()`:**
- Takes a screenshot via `adb_manager.screencap()`
- Queries ROIs `own_gold_number`, `own_elixir_number`, `own_dark_elixir_number`, `own_gems_number` from DB
- Calls `read_number()` on each ROI
- Stores results in instance variables
- Falls back to 0 if any ROI is missing or OCR returns None

**Modified `get_status_dict()`:**
Add four new fields:
```python
"current_gold": self.current_gold,
"current_elixir": self.current_elixir,
"current_dark_elixir": self.current_dark_elixir,
"current_gems": self.current_gems,
```

**Call sites:**
- `_run()` farming loop — after returning to base (call `read_current_resources()`)
- `_run()` upgrade loop — after returning to base (call `read_current_resources()`)

#### `backend/api/ws_status.py`

Add `"read_resources"` command handling:
```python
elif cmd == "read_resources":
    await sequence_runner.read_current_resources()
```

#### `backend/db/database.py`

Add seed ROI for gems in `seed_default_rois()`:
```python
RoiTemplate(roi_name="own_gems_number", x_pos=1078, y_pos=195, width=80, height=30, description="Own gems count (top-right)")
```
Coordinates are estimated — user fine-tunes via Calibrator tab.

### Frontend Changes

#### `frontend/src/main.js`

**New Alpine state variables:**
```js
currentGold: 0,
currentElixir: 0,
currentDarkElixir: 0,
currentGems: 0,
```

**Modified `connectBotStatus()` — consume resource fields:**
```js
this.currentGold = data.current_gold ?? 0;
this.currentElixir = data.current_elixir ?? 0;
this.currentDarkElixir = data.current_dark_elixir ?? 0;
this.currentGems = data.current_gems ?? 0;
```

**Modified `connectAdb()` — trigger read after connect:**
After successful connect, send `{ command: "read_resources" }` via `wsBotStatus` WebSocket.

**`loadFarmingConfig()` — no changes needed** (threshold config is separate from current balance display).

#### `frontend/index.html`

Replace Summary panel placeholder values with live bindings:
```html
<div class="bg-slate-800 rounded-lg border border-slate-700 p-4">
  <h3 class="text-xs uppercase tracking-wider text-slate-400 mb-2">Resources</h3>
  <div class="text-sm space-y-1">
    <div class="flex justify-between"><span>Gold</span><span class="text-yellow-400" x-text="currentGold"></span></div>
    <div class="flex justify-between"><span>Elixir</span><span class="text-pink-400" x-text="currentElixir"></span></div>
    <div class="flex justify-between"><span>Dark E.</span><span class="text-purple-400" x-text="currentDarkElixir"></span></div>
    <div class="flex justify-between"><span>Gems</span><span class="text-green-400" x-text="currentGems"></span></div>
  </div>
</div>
```

### ROI Summary

| ROI name | x | y | w | h | Source |
|---|---|---|---|---|---|
| `own_gold_number` | 1013 | 25 | 194 | 37 | Existing (DB) |
| `own_elixir_number` | 1013 | 94 | 196 | 35 | Existing (DB) |
| `own_dark_elixir_number` | 1078 | 162 | 133 | 33 | Existing (DB) |
| `own_gems_number` | 1078 | 195 | 80 | 30 | **New** (seed estimate) |

### Error Handling

- If `adb_manager` is not connected, `read_current_resources()` returns early (keeps previous values)
- If any ROI is missing from DB, that field stays at 0
- If OCR returns None/crashes, individual field falls back to 0
- WebSocket disconnect: frontend keeps last known values
- If `wsBotStatus` is not connected when trying to send `read_resources` command, silently skip

### Files Changed

| File | Change |
|---|---|
| `backend/engine/sequence_runner.py` | Add `current_*` vars, `read_current_resources()`, modify `get_status_dict()`, call at loop ends |
| `backend/api/ws_status.py` | Add `read_resources` command handler |
| `backend/db/database.py` | Seed `own_gems_number` ROI |
| `frontend/src/main.js` | Add resource state vars, consume in `connectBotStatus()`, send command after ADB connect |
| `frontend/index.html` | Replace Summary panel bindings |
