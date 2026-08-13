# Lab Upgrade Fix — Design Spec

**Date:** 2026-08-13
**Status:** Approved

## Problem

Two defects remain in the laboratory (lab) upgrade flow:

1. **Lab status pre-check always returns `"unknown"`.** `_read_lab_status` uses a single
   grayscale OCR pass over the wide `lab_upgrade` ROI (icon + text). The top-bar background
   has low contrast and the `/` separator is thin, so EasyOCR reads `"1/1"` / `"0/1"` as
   `"I1"`. The regex requires `digit/digit`, so it fails and returns `"unknown"`. The method
   also lacks the OTSU + erode fallback that `read_number` already uses successfully for the
   builder count.

2. **Red confirm button is not handled correctly.** When `analyze_confirm_button` returns
   `INSUFFICIENT_RESOURCES` (red cost text) or `TOWN_HALL_REQUIRED`, the bot should not tap
   Confirm. Instead it should close the modal twice and return to farming mode. Currently the
   lab flow only logs "skipping" and closes once.

## Solution

- Add a robust `read_ratio` OCR helper (used/total) mirroring `read_number` preprocessing.
- Add a dedicated narrow `lab_status` ROI for reading only the `0/1` digits, separate from the
  `lab_upgrade` ROI used for tapping the lab icon.
- Gate lab routing so the lab is only attempted when status is `"free"`. `"busy"` and
  `"unknown"` both skip the lab and stay in farming.
- Fix `_do_lab_upgrade` confirm handling: red/TH-required → close twice + farming; not found
  → close once.

## Design

### 1. New helper `read_ratio` in `backend/vision/ocr.py`

```python
def read_ratio(screenshot, x, y, width, height, roi_name="") -> tuple[int, int] | None:
    """Read a 'used/total' ratio (e.g. '0/1', '2/5') from a region.

    Returns (used, total) or None if unreadable.
    """
```

Internal steps:

1. Crop the ROI (using the same `_get_padding` padding logic as `read_number`), resize 2x with
   `INTER_NEAREST`, convert to grayscale.
2. Pass 1: `reader.readtext(gray, detail=0, paragraph=True)` → join text → regex
   `(\d+)\s*/\s*(\d+)`.
3. Pass 2 (if no match): OTSU threshold + erode → `readtext` → regex again.
4. Pass 3 (if still no match): split the ROI vertically into left/right halves and call
   `read_number` on each half, returning `(left, right)`.
5. Any exception → `None`.

This reuses the proven `read_number` preprocessing path rather than inventing a new one.

### 2. New ROI `lab_status`

- `lab_upgrade` ROI stays unchanged and is used **only** for tapping the lab icon.
- `lab_status` is a new ROI calibrated to **only** the `0/1` digits (no icon).
- If `lab_status` is not calibrated, `_read_lab_status` returns `"unknown"` safely.

### 3. `_read_lab_status` in `sequence_runner.py`

Replace the current inline crop + single-pass OCR:

- Query `lab_status` ROI. If missing → return `"unknown"`.
- Call `read_ratio(screen, x, y, w, h, roi_name="lab_status")`.
- `(used, total)`: `used == 0` → `"free"`, otherwise `"busy"`.
- `None` → `"unknown"`.
- Keep debug crops for verification: `storage/debug/lab_status_crop.png` and
  `storage/debug/lab_status_gray.png`.

### 4. Routing gates

**`_do_upgrade_check`** (`builders < 1` branch):

```
lab_status = _read_lab_status(screen)
if lab_status == "free":
    _upgrade_target = {"type": "lab"}
else:  # busy or unknown
    _upgrade_target = None
```

**`_evaluate_mode`** (`builders < 1` branch):

```
lab_status = _read_lab_status(screen)
if lab_status == "free":
    _upgrade_target = {"type": "lab"}
    return "upgrade"
return "farming"
```

**`_do_upgrade_execute`** top guard:

```python
if not self._upgrade_target:
    logger.info("No upgrade target — skipping")
    return
```

This prevents the execute step from falling through to the building-upgrade flow when the lab
was skipped.

### 5. Confirm button handling in `_do_lab_upgrade`

Also update the existing lab pre-check inside `_do_lab_upgrade` so `"unknown"` behaves the same
as `"busy"` (skip the lab instead of proceeding to tap):

```python
if status in ("busy", "unknown"):
    logger.info("Lab not available (%s) — skipping", status)
    self._upgrade_target = None
    return
```

Add a small helper:

```python
async def _tap_close_universal(self, adb):
    with get_session() as session:
        close_roi = session.query(RoiTemplate).filter_by(
            roi_name="btn_close_universal").first()
    if close_roi:
        cx = close_roi.x_pos + close_roi.width // 2
        cy = close_roi.y_pos + close_roi.height // 2
        await human_tap(adb, cx, cy, sigma=5)
    else:
        logger.warning("btn_close_universal ROI not calibrated")
```

Phase 3 behavior:

| `analyze_confirm_button` status | Action |
|---|---|
| `READY` | Tap Confirm, delay, close once, clear `_upgrade_target`, finish |
| `INSUFFICIENT_RESOURCES` | Do not tap Confirm; close twice (0.5–1s delay each); set `_loop_mode = "farming"`; clear `_upgrade_target`; return |
| `TOWN_HALL_REQUIRED` | Same as `INSUFFICIENT_RESOURCES` |
| `NOT_FOUND` | Close once; clear `_upgrade_target`; return |

### 6. Error handling

| Scenario | Outcome |
|---|---|
| `lab_status` ROI not calibrated | `_read_lab_status` → `"unknown"` → skip lab |
| `read_ratio` OCR fails | Returns `None` → `"unknown"` → skip lab |
| Red confirm (insufficient resources) | No Confirm tap; close 2x; switch farming |
| TH required | No Confirm tap; close 2x; switch farming |
| Confirm not found | Close 1x; clear target |

`"unknown"` is treated identically to `"busy"`: the lab is not attempted, and the worst case is
a one-cycle missed upgrade that retries later.

## Files modified

| File | Change |
|---|---|
| `backend/vision/ocr.py` | Add `read_ratio` helper |
| `backend/engine/sequence_runner.py` | Replace `_read_lab_status`, add routing gates, add `_tap_close_universal`, fix `_do_lab_upgrade` confirm flow |

## Verification

1. Calibrate `lab_status` ROI to only the `0/1` digits.
2. Restart bot and confirm log shows `Lab status: free/busy/unknown` (not always `unknown`).
3. Lab free (`0/1`) → bot opens the lab panel and attempts the upgrade.
4. Red confirm (insufficient resources) → bot does **not** tap Confirm, closes twice, and
   `_loop_mode` becomes `farming`.
5. Lab busy (`1/1`) or unknown → bot skips the lab and stays in farming.
