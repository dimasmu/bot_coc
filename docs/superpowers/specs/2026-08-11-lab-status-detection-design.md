# Lab Status Detection — Design Spec

**Date:** 2026-08-11
**Status:** Approved

## Problem

The bot routes to `_do_lab_upgrade` whenever `builders < 1`, without checking whether
the laboratory is actually free. If the lab is busy researching, the bot:
1. Taps the lab icon (disrupting game state)
2. Fails to find "Suggested" text in the research panel
3. Falls back silently

This wastes a cycle every time and can leave the bot in a wrong screen state.

## Solution

Add `_read_lab_status(screen)` — an OCR-based status read on the calibrated `lab_upgrade`
ROI, following the exact pattern of `_read_builder_count`. Gate all lab routing behind
this check.

## Design

### New method: `_read_lab_status(screen) → str`

File: `backend/engine/sequence_runner.py`

```python
def _read_lab_status(self, screen) -> str:
    """Read lab research status from top bar.

    OCRs the lab_upgrade ROI looking for an X/Y pattern (e.g. '0/1' or '1/1').

    Returns:
        'free'  — 0/1, lab available for research
        'busy'  — 1/1, research in progress
        'unknown' — OCR failed or ROI not calibrated (safe fallback)
    """
    import re
    from backend.vision.ocr import read_raw_text

    with get_session() as session:
        lab_roi = session.query(RoiTemplate).filter_by(
            roi_name="lab_upgrade").first()
    if not lab_roi:
        return "unknown"

    text = read_raw_text(screen, lab_roi.x_pos, lab_roi.y_pos,
                         lab_roi.width, lab_roi.height)
    if not text:
        return "unknown"

    m = re.search(r'(\d+)\s*/\s*(\d+)', text)
    if m:
        used = int(m.group(1))
        if used == 0:
            return "free"
        return "busy"

    return "unknown"
```

Reuses the existing `lab_upgrade` ROI (id=45, x=408, y=23, 137x40), which
covers the lab icon + status text area in the top bar.

### Routing changes

**`_evaluate_mode`** — add lab status check before routing to lab upgrade:

```
Before: builders < 1 → always route to lab
After:  builders < 1 → check lab_status → only route if "free"
```

**`_do_upgrade_check`** — same gate:

```
Before: builders < 1 → always set {"type": "lab"}
After:  builders < 1 → check lab_status → only set if "free"
```

**`_do_lab_upgrade`** — belt-and-suspenders pre-check before tapping:

```
Add at top: read lab_status → if "busy", log and return immediately
```

### Error handling

| Scenario | Outcome |
|---|---|
| ROI not calibrated | `_read_lab_status` returns `"unknown"` → skips lab, stays in farming |
| OCR returns garbage (e.g. "I/I") | Regex fails to match → `"unknown"` → falls through |
| Lab not built (no icon) | OCR returns empty/garbage → `"unknown"` → farming |
| Lab is free | Returns `"free"` → routes to `_do_lab_upgrade` as before |

The `"unknown"` fallback is safe: the worst case is a missed upgrade opportunity
for one cycle, which retries automatically on the next loop.

## Files modified

| File | Change |
|---|---|
| `backend/engine/sequence_runner.py` | Add `_read_lab_status` method, gate 3 routing sites |

## Verification

1. Lab free (`0/1`): `_read_lab_status` returns `"free"` → `_do_lab_upgrade` executes
2. Lab busy (`1/1`): `_read_lab_status` returns `"busy"` → bot stays in farming
3. Lab not built: `_read_lab_status` returns `"unknown"` → no lab attempt
4. `builders < 1` + lab free → upgrade mode set correctly
5. `builders < 1` + lab busy → bot stays in farming without disrupting game state
