# Builder OCR X/Y + Loop Mode Indicator — Design Spec

**Date**: 2026-08-02
**Status**: Approved
**Goal**: (1) Make builder count detection more reliable by reading "X/Y" format instead of isolated digits. (2) Show current loop mode (farming/upgrade) in the dashboard Bot Status panel.

## Problem

1. `_read_builder_count()` uses `read_number()` with digit whitelist on a small ROI. OCR frequently misreads (e.g., "0" → "75"). The `> 6` guard normalizes it to 1, so AI is called unnecessarily when all builders are busy. This wastes an expensive DashScope call.

2. No frontend indicator shows which mode the bot is currently running (farming or upgrade).

## Design

### Feature 1: Builder OCR using "X/Y" format

**File:** `backend/engine/sequence_runner.py`

Replace `_read_builder_count()` (line 468-489) with a new implementation:

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
        logger.info("Builder count OCR: '%s' → %d", text, bc)
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

**Flow:**
1. Crop ROI → upscale 2x
2. EasyOCR without digit whitelist → `paragraph=True` to combine text
3. Regex `(\d+)\s*/\s*\d+` extracts the count before `/`
4. Validate: > 6 → misread → default 1
5. If no `X/Y` pattern found: fallback to `read_number()` legacy behavior

### Feature 2: Loop Mode Indicator in Dashboard

**Backend — `sequence_runner.py`:**

Add `_loop_mode` instance variable in `__init__`:
```python
self._loop_mode = ""  # "farming" or "upgrade"
```

Set it in `_run()` after evaluate:
```python
if self._running:
    self._loop_mode = current_mode  # store for status display
    await self.read_current_resources()
```

Add to `get_status_dict()`:
```python
"loop_mode": self._loop_mode,
```

**Frontend — `main.js`:**

Add state var:
```js
loopMode: "",
```

Consume in `connectBotStatus()`:
```js
this.loopMode = data.loop_mode ?? "";
```

**Frontend — `index.html`:**

Replace Bot Status header area to include mode label inline with status badge:

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
            <select x-model="activeSequenceId" class="w-full mt-2 bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs">
              ...
```

**Colors:** Farming = amber, Upgrade = purple. Hidden when empty (bot not running).

### Files Changed

| File | Change |
|---|---|
| `backend/engine/sequence_runner.py` | Replace `_read_builder_count()` with X/Y OCR |
| `backend/engine/sequence_runner.py` | Add `_loop_mode` var, set in `_run()`, include in `get_status_dict()` |
| `frontend/src/main.js` | Add `loopMode` state, consume from WS |
| `frontend/index.html` | Add mode label next to status badge |
