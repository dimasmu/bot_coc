# Builder OCR X/Y + Loop Mode Indicator — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make builder count detection reliable by reading "X/Y" format via EasyOCR, and show current loop mode (FARMING/UPGRADE) in the dashboard Bot Status panel.

**Architecture:** Replace `_read_builder_count()` to use full-text EasyOCR + regex `X/Y` parsing with digit-only fallback. Add `_loop_mode` tracking to `SequenceRunner` and expose via `get_status_dict()`. Frontend consumes via existing `/ws/status` WebSocket and renders a colored label.

**Tech Stack:** Python (EasyOCR, OpenCV, asyncio), JavaScript (Alpine.js)

---

### Task 1: Replace _read_builder_count() with X/Y OCR

**Files:**
- Modify: `backend/engine/sequence_runner.py:468-489`

- [ ] **Step 1: Read current _read_builder_count() method**

Read lines 468-489 of `C:\programming\python\backend\engine\sequence_runner.py` to confirm the exact method boundaries.

- [ ] **Step 2: Replace the method**

Replace the entire `_read_builder_count()` method with:

```python
    def _read_builder_count(self, screen) -> int:
        """OCR builder count from format 'X/Y' (e.g. '2/5'). Defaults to 1 if misread."""
        import re
        from backend.vision.ocr import _get_reader

        with get_session() as session:
            builder_roi = session.query(RoiTemplate).filter_by(roi_name="builder_count").first()
        if not builder_roi:
            return 1

        nparr = np.frombuffer(screen, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        h_img, w_img = img.shape[:2]
        x1 = max(0, builder_roi.x_pos)
        y1 = max(0, builder_roi.y_pos)
        x2 = min(w_img, x1 + builder_roi.width)
        y2 = min(h_img, y1 + builder_roi.height)
        roi = img[y1:y2, x1:x2]
        roi = cv2.resize(roi, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)

        reader = _get_reader()
        results = reader.readtext(roi, detail=0, paragraph=True)
        text = " ".join(results).strip() if results else ""

        m = re.search(r'(\d+)\s*/\s*\d+', text)
        if m:
            bc = int(m.group(1))
            if bc > 6:
                logger.warning("Builder count OCR returned %d (recalibrate ROI)", bc)
                return 1
            logger.info("Builder count OCR: '%s' -> %d", text, bc)
            return bc

        # Fallback: legacy digit-only OCR
        from backend.vision.ocr import read_number
        bc = read_number(screen, builder_roi.x_pos, builder_roi.y_pos,
                         builder_roi.width, builder_roi.height, roi_name="builder_count")
        if bc is None:
            return 1
        if bc > 6:
            logger.warning("Builder count OCR returned %d (recalibrate ROI)", bc)
            return 1
        return bc
```

- [ ] **Step 3: Verify Python syntax**

```bash
python -c "import ast; ast.parse(open('backend/engine/sequence_runner.py').read()); print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/engine/sequence_runner.py
git commit -m "feat: use X/Y format OCR for builder count detection"
```

---

### Task 2: Add _loop_mode tracking and expose in status

**Files:**
- Modify: `backend/engine/sequence_runner.py` — `__init__`, `_run()`, `get_status_dict()`

- [ ] **Step 1: Add _loop_mode instance variable**

In `SequenceRunner.__init__()`, add after `self._upgrade_target = None` (around line 31):

```python
        self._loop_mode = ""  # "farming" or "upgrade"
```

- [ ] **Step 2: Set _loop_mode in _run()**

In `_run()`, find the block after `_evaluate_mode()` (around line 118-120):

Current:
```python
        if self._running:
            current_mode = await self._evaluate_mode(adb)
            await self.read_current_resources()
```

Replace with:
```python
        if self._running:
            current_mode = await self._evaluate_mode(adb)
            self._loop_mode = current_mode
            await self.read_current_resources()
```

- [ ] **Step 3: Add loop_mode to get_status_dict()**

In `get_status_dict()`, add the new key:

```python
"loop_mode": self._loop_mode,
```

- [ ] **Step 4: Verify Python syntax**

```bash
python -c "import ast; ast.parse(open('backend/engine/sequence_runner.py').read()); print('OK')"
```
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add backend/engine/sequence_runner.py
git commit -m "feat: add loop_mode tracking and expose in status dict"
```

---

### Task 3: Frontend — add loopMode state and consume from WS

**Files:**
- Modify: `frontend/src/main.js`

- [ ] **Step 1: Add loopMode state variable**

After `currentGems: 0,` (around line 37), add:

```js
    loopMode: "",
```

- [ ] **Step 2: Consume loop_mode in connectBotStatus()**

In `connectBotStatus()`, after `this.currentGems = data.current_gems ?? 0;`, add:

```js
        this.loopMode = data.loop_mode ?? "";
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/main.js
git commit -m "feat: consume loop_mode from WS status in frontend"
```

---

### Task 4: Frontend — add loop mode label to Bot Status panel

**Files:**
- Modify: `frontend/index.html` — Bot Status card (lines 84-105)

- [ ] **Step 1: Replace Bot Status header section**

Read lines 84-89 of `C:\programming\python\frontend\index.html`. The current code:

```html
          <div class="bg-slate-800 rounded-lg border border-slate-700 p-4">
            <h3 class="text-xs uppercase tracking-wider text-slate-400 mb-2">Bot Status</h3>
            <span class="inline-block px-2 py-1 rounded text-xs font-bold"
              :class="botState === 'STOPPED' ? 'bg-slate-700 text-slate-300' : botState === 'DEAD' ? 'bg-red-900 text-red-300' : 'bg-green-900 text-green-300'"
              x-text="botState">IDLE</span>
            <select x-model="activeSequenceId" class="w-full mt-2 bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs">
```

Replace with:

```html
          <div class="bg-slate-800 rounded-lg border border-slate-700 p-4">
            <h3 class="text-xs uppercase tracking-wider text-slate-400 mb-2">Bot Status</h3>
            <div class="flex items-center gap-2 mb-2">
              <span class="inline-block px-2 py-1 rounded text-xs font-bold"
                :class="botState === 'STOPPED' ? 'bg-slate-700 text-slate-300' : botState === 'DEAD' ? 'bg-red-900 text-red-300' : 'bg-green-900 text-green-300'"
                x-text="botState">IDLE</span>
              <span x-show="loopMode" class="px-2 py-1 rounded text-xs font-bold"
                :class="loopMode === 'farming' ? 'bg-amber-900 text-amber-300' : 'bg-purple-900 text-purple-300'"
                x-text="loopMode === 'farming' ? 'FARMING' : 'UPGRADE'"></span>
            </div>
            <select x-model="activeSequenceId" class="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs">
```

Note: Also remove `mt-2` from the select since the flex container now has `mb-2`.

- [ ] **Step 2: Commit**

```bash
git add frontend/index.html
git commit -m "feat: add loop mode indicator label to Bot Status panel"
```

---

### Task 5: Rebuild frontend and verify

- [ ] **Step 1: Rebuild**

```bash
cd frontend && npm run build
```

- [ ] **Step 2: Verify key bindings in dist output**

```bash
grep -c "loopMode" dist/index.html
```
Expected: `2` or more (one x-show, one x-text binding)

```bash
grep -c "filteredLogLines" dist/index.html
```
Expected: `1` (ensure previous fix not regressed)

```bash
grep -c "currentGold" dist/index.html
```
Expected: `1` (ensure resource display not regressed)
