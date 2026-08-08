# New Building Purchase & Deploy Design

## Problem

When the upgrade system finds a "new" (unpurchased) building under "Suggested Upgrades" and taps the first row, the game opens the **Shop screen** instead of an upgrade panel. The current code looks for hammer/cog upgrade buttons, finds none, and silently gives up.

## Solution

Detect the Shop screen after tapping the Suggested row, then route to a dedicated purchase + deploy flow.

### Indicators (user-provided)

| Element | Location | Purpose |
|---------|----------|---------|
| Shop tab icon (hammer/saw) | Top bar of Shop screen | Confirm we're in Shop |
| Green arrow on building card | Shop screen, overlaid on the highlighted building | Identifies which building to buy |
| ✓ (checkmark) | Deploy screen, bottom-right | Confirm placement |
| ✗ (cross) | Deploy screen, opposite side | Cancel placement |
| Building base turns red | Bottom of building preview on deploy screen | Collision with obstacle/building |
| Building base turns green | Bottom of building preview | Valid placement |

## Architecture

### Integration point

In `_do_upgrade_execute`, after tapping the Suggested row (Phase 1), take a screenshot BEFORE closing the builder menu:

- **Shop icon detected** → route to `_do_new_building_purchase(adb)` → return
- **Shop icon NOT detected** → close menu, continue with existing hammer/cog flow (unchanged)

The `_do_new_building_purchase` method is standalone and returns `True` on success, `False` on failure. The caller (`_do_upgrade_execute`) sets `_upgrade_target = None` on failure and the step retry loop in `_run` handles recovery.

### Full flow

```
Builder Menu → Tap Suggested row → Wait 1-1.5s → Screencap
  │
  ├─ Shop icon NOT found → close menu → hammer/cog → upgrade (existing)
  │
  └─ Shop icon FOUND → _do_new_building_purchase(adb)
       │
       ├─ Phase A: Find arrow in Shop
       │   1. Template match "shop_arrow_green.png" (threshold 0.6)
       │   2. If matched → tap arrow center (sigma 5), go to Phase B
       │   3. If NOT matched → Qwen AI fallback:
       │      - Send Shop screencap to AI
       │      - Prompt: "Find the highlighted building card with the green arrow.
       │        Return only the (x,y) pixel coordinates of the arrow's center."
       │      - Parse (x,y), tap, go to Phase B
       │   4. Wait 1-2s for deploy transition
       │
       ├─ Phase B: Verify deploy screen
       │   1. Screencap, match_template("icon_shop_tab.png", 0.6)
       │   2. If still found → Phase A tap missed, retry Qwen once, then return False
       │   3. If NOT found → we're on deploy screen, proceed to Phase C
       │
       ├─ Phase C: Drag until valid placement (max 20 iterations)
       │   1. Screencap, template match "btn_deploy_checkmark.png" for position
       │   2. Sample checkmark ROI center: count green pixels (G>150, G>R, G>B)
       │   3. If green pixel count > threshold → placement valid, go to Phase D
       │   4. If NOT green (greyed out = collision):
       │      - adb shell input swipe from center to (center + random offset)
       │      - dx in [-250, 250], dy in [-200, 200], duration 200ms
       │      - Wait 0.4s, go to step 1
       │   5. After 20 attempts still grey:
       │      - Tap ✗ to cancel
       │      - Log warning, return False
       │
       └─ Phase D: Confirm placement
           1. Tap checkmark ✓ (already found position from Phase C)
           2. Wait 0.5s
           3. Log success, return True
```

## Templates Required

User must capture these from the emulator and save to `storage/templates/`:

| File | What to capture | Size guidance | Threshold |
|------|----------------|---------------|-----------|
| `shop_arrow_green.png` | The green arrow tip on the highlighted building card in Shop (avoid capturing the building image behind it — crop tightly around the arrow) | ~30x30px | 0.6 |
| `btn_deploy_checkmark.png` | The green ✓ button at bottom of deploy screen | ~30x30px | Use for position only |

## Checkmark Color Detection

The checkmark template is used to **find the button position**. To determine if placement is valid, we sample the template match area's pixels:

```python
# Sample the region where checkmark was matched
green_pixels = 0
for pixel in roi_area:
    r, g, b = pixel
    if g > 150 and g > r * 1.1 and g > b * 1.1:
        green_pixels += 1

valid = green_pixels > (total_pixels * 0.15)  # 15% of area is green → active
```

This distinguishes a green (active) checkmark from a greyed-out (invalid placement) one.

## Error Handling

| Failure | Action |
|---------|--------|
| Phase A: Arrow template not found, Qwen also fails | Return False → step retry |
| Phase B: Still in Shop after tapping | Return False → step retry (returns home, retries step) |
| Phase C: 20 drags, still red/grey | Tap ✗ cancel, return False → step retry |
| Phase D: Checkmark missing | Return False → step retry |
| Any unexpected exception | Caught by `_run` retry loop (existing) |

Step retry behavior (existing in `_run`):
1. First failure of a step: retry the same step
2. Second failure: return home, skip to next step

## New Dependencies

- `match_template()` — already imported
- `human_tap()` — already imported
- `adb.screencap()` — already available
- `adb_swipe()` — new helper needed in `AdbManager` for drag
- Qwen AI client — already available via `backend/ai.py`
- `_verify_home_screen()` — already exists

### ADB Swipe Helper

Add to `AdbManager`:
```python
async def swipe(self, x1: int, y1: int, x2: int, y2: int,
                duration_ms: int = 200) -> bool:
    """Perform a swipe/drag via ADB input swipe."""
    if self._serial is None:
        return False
    try:
        await self._run_adb(
            "-s", self._serial, "shell", "input", "swipe",
            str(x1), str(y1), str(x2), str(y2), str(duration_ms),
        )
        return True
    except Exception as e:
        logger.error("swipe failed: %s", e)
        return False
```

## Files Changed

| File | Change |
|------|--------|
| `backend/engine/sequence_runner.py` | Modify Phase 1 of `_do_upgrade_execute` + add `_do_new_building_purchase()` |
| `backend/adb/manager.py` | Add `swipe()` method |
| `storage/templates/` | User adds `shop_arrow_green.png`, `btn_deploy_checkmark.png` |

No frontend changes needed.

## Logging

All steps log at INFO level:
- `"New building detected — entering purchase flow"` (Phase A entry)
- `"Arrow found via template at (x,y)"` (Phase A success)
- `"Arrow found via Qwen at (x,y)"` (Phase A fallback)
- `"Checking placement validity — attempt X/20"` (Phase C loop)
- `"Placement valid after X drags"` (Phase C success)
- `"New building purchased and deployed"` (Phase D success)
- `"Failed to place building after 20 attempts — cancelling"` (Phase C fail)

## What this does NOT handle

- **Shop category navigation**: Assumes the Shop opens directly on the correct building (pre-selected). If Shop opens on a different category, this flow fails.
- **Resource insufficient**: Does not check if the player has enough gold/elixir to buy the building. The game will show an error, and the flow will fail at Phase A (no highlighted arrow).
- **Multiple new buildings**: If more than one new building is available, the Suggested row might point to any of them. The flow only handles one per iteration.
- **Trap deployment**: Traps have a different Shop UI (smaller, different placement mechanic). Not in scope.
