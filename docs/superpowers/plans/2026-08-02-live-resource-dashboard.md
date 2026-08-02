# Live Resource Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show current resource balances (gold, elixir, dark elixir, gems) live in the Dashboard Summary panel, updated after every farming/upgrade loop and on ADB connect.

**Architecture:** Add `read_current_resources()` async method to `SequenceRunner` that screenshots + OCRs own-base resource ROIs and stores in instance vars. Include these in `get_status_dict()` sent via existing `/ws/status` WebSocket. Add `read_resources` WS command for on-demand triggers. Frontend consumes new fields and renders in Summary panel.

**Tech Stack:** Python (FastAPI, EasyOCR, OpenCV), JavaScript (Alpine.js), SQLite/SQLModel

---

### Task 1: Add current resource instance vars and update get_status_dict

**Files:**
- Modify: `backend/engine/sequence_runner.py:26-46`

- [ ] **Step 1: Add instance variables to SequenceRunner.__init__**

Add after `self.raids_completed = 0` at line 30:

```python
self.current_gold = 0
self.current_elixir = 0
self.current_dark_elixir = 0
self.current_gems = 0
```

- [ ] **Step 2: Update get_status_dict() to include resource fields**

Replace `get_status_dict()` (lines 38-46) with:

```python
def get_status_dict(self):
    return {
        "state": self.state,
        "running": self._running,
        "gold_earned": self.gold_earned,
        "elixir_earned": self.elixir_earned,
        "dark_elixir_earned": self.dark_elixir_earned,
        "raids_completed": self.raids_completed,
        "current_gold": self.current_gold,
        "current_elixir": self.current_elixir,
        "current_dark_elixir": self.current_dark_elixir,
        "current_gems": self.current_gems,
    }
```

- [ ] **Step 3: Commit**

```bash
git add backend/engine/sequence_runner.py
git commit -m "feat: add current resource instance vars to SequenceRunner status"
```

---

### Task 2: Add read_current_resources() method

**Files:**
- Modify: `backend/engine/sequence_runner.py` (after `_read_resources` method, around line 429)

- [ ] **Step 1: Add async method read_current_resources()**

Insert after `_read_resources()` return at line 429:

```python
async def read_current_resources(self):
    """Take a screenshot and OCR own-base resources into instance variables."""
    adb = adb_manager
    if not adb.is_connected:
        return
    screen = await adb.screencap()
    if not screen:
        return

    from backend.vision.ocr import read_number
    with get_session() as session:
        gold_roi = session.query(RoiTemplate).filter_by(roi_name="own_gold_number").first()
        elixir_roi = session.query(RoiTemplate).filter_by(roi_name="own_elixir_number").first()
        de_roi = session.query(RoiTemplate).filter_by(roi_name="own_dark_elixir_number").first()
        gems_roi = session.query(RoiTemplate).filter_by(roi_name="own_gems_number").first()

    self.current_gold = gold_roi and (read_number(screen, gold_roi.x_pos, gold_roi.y_pos,
        gold_roi.width, gold_roi.height, roi_name=gold_roi.roi_name) or 0)
    self.current_elixir = elixir_roi and (read_number(screen, elixir_roi.x_pos, elixir_roi.y_pos,
        elixir_roi.width, elixir_roi.height, roi_name=elixir_roi.roi_name) or 0)
    self.current_dark_elixir = de_roi and (read_number(screen, de_roi.x_pos, de_roi.y_pos,
        de_roi.width, de_roi.height, roi_name=de_roi.roi_name) or 0)
    self.current_gems = gems_roi and (read_number(screen, gems_roi.x_pos, gems_roi.y_pos,
        gems_roi.width, gems_roi.height, roi_name=gems_roi.roi_name) or 0)

    logger.info("Resources read: G=%d E=%d DE=%d Gems=%d",
        self.current_gold, self.current_elixir, self.current_dark_elixir, self.current_gems)
```

- [ ] **Step 2: Commit**

```bash
git add backend/engine/sequence_runner.py
git commit -m "feat: add read_current_resources() method for live resource OCR"
```

---

### Task 3: Call read_current_resources() at end of each bot loop

**Files:**
- Modify: `backend/engine/sequence_runner.py:111-112`

- [ ] **Step 1: Add call after evaluate_mode in _run()**

At line 112, inside the `if self._running:` block, add `await self.read_current_resources()` after `_evaluate_mode()`:

```python
        if self._running:
            current_mode = await self._evaluate_mode(adb)
            await self.read_current_resources()
```

- [ ] **Step 2: Commit**

```bash
git add backend/engine/sequence_runner.py
git commit -m "feat: read current resources after each bot loop"
```

---

### Task 4: Add read_resources command to WebSocket status

**Files:**
- Modify: `backend/api/ws_status.py:35-36`

- [ ] **Step 1: Add command handler in status_stream()**

Add after the `elif cmd == "pause":` block at line 36:

```python
                elif cmd == "read_resources":
                    await sequence_runner.read_current_resources()
```

- [ ] **Step 2: Commit**

```bash
git add backend/api/ws_status.py
git commit -m "feat: add read_resources WS command for on-demand resource reading"
```

---

### Task 5: Seed own_gems_number ROI in database

**Files:**
- Modify: `backend/db/database.py:57`

- [ ] **Step 1: Add gems ROI to seed_default_rois()**

Add to `roi_defaults` list after the `builder_count` line at line 57:

```python
        ("own_gems_number", "read", 1078, 195, 80, 30),
```

- [ ] **Step 2: Commit**

```bash
git add backend/db/database.py
git commit -m "feat: add own_gems_number seed ROI for resource reading"
```

---

### Task 6: Frontend — add resource state variables and consume from WS

**Files:**
- Modify: `frontend/src/main.js:32-33` (state vars)
- Modify: `frontend/src/main.js:178-189` (connectBotStatus)

- [ ] **Step 1: Add resource state variables**

Add after `botRunning: false` at line 33:

```js
    currentGold: 0,
    currentElixir: 0,
    currentDarkElixir: 0,
    currentGems: 0,
```

- [ ] **Step 2: Consume resource fields in connectBotStatus()**

Replace `connectBotStatus()` (lines 178-189) with:

```js
    connectBotStatus() {
      if (this.wsBotStatus) return;
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${proto}//${location.host}/ws/status`);
      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        this.botState = data.state;
        this.botRunning = data.running;
        this.currentGold = data.current_gold ?? 0;
        this.currentElixir = data.current_elixir ?? 0;
        this.currentDarkElixir = data.current_dark_elixir ?? 0;
        this.currentGems = data.current_gems ?? 0;
      };
      ws.onclose = () => { this.wsBotStatus = null; };
      this.wsBotStatus = ws;
    },
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/main.js
git commit -m "feat: consume live resource fields from WS status"
```

---

### Task 7: Frontend — trigger read_resources after ADB connect

**Files:**
- Modify: `frontend/src/main.js:131-150` (connectAdb)

- [ ] **Step 1: Send read_resources command after successful ADB connect**

In `connectAdb()`, after `this.adbStatus = data.status;` at line 143, add:

```js
        if (this.adbStatus.connected && this.wsBotStatus && this.wsBotStatus.readyState === WebSocket.OPEN) {
          this.wsBotStatus.send(JSON.stringify({ command: "read_resources" }));
        }
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/main.js
git commit -m "feat: trigger resource read after ADB connect"
```

---

### Task 8: Frontend — replace Summary panel with live resource bindings

**Files:**
- Modify: `frontend/index.html:106-115`

- [ ] **Step 1: Replace Summary panel markup**

Replace the entire Summary panel div (lines 106-115):

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

- [ ] **Step 2: Commit**

```bash
git add frontend/index.html
git commit -m "feat: bind Summary panel to live resource values"
```

---

### Task 9: Rebuild frontend and verify

**Files:**
- (dist/ — generated)

- [ ] **Step 1: Rebuild frontend**

```bash
cd frontend && npm run build
```

- [ ] **Step 2: Verify build output**

Check that `dist/index.html` contains `currentGold` / `currentElixir` / `currentDarkElixir` / `currentGems` bindings, and `dist/assets/index-*.js` includes the new code.

```bash
grep -c "currentGold" dist/index.html
# Expected: 1
grep -c "filteredLogLines" dist/index.html
# Expected: 1 (ensure previous fix not regressed)
```

- [ ] **Step 3: Commit dist**

```bash
git add frontend/dist/
git commit -m "build: rebuild frontend with live resource dashboard"
```
