# Screen Verification & Recovery Design

## Problem

The bot operates **open-loop**: it taps coordinates blindly without ever checking whether the expected screen actually appeared. A single misclick (Shop instead of Next, gacha popup instead of village) cascades into a completely broken sequence because every subsequent step runs on the wrong screen.

### Known failure scenarios

| # | When | What happens | Impact |
|---|------|-------------|--------|
| 1 | **Farming search loop** — tapping "Next" | Misclick hits Shop icon → search loop runs on Shop screen, never finds a target. After max searches, it "attacks" from Shop screen. | Entire farming sequence wasted. |
| 2 | **After return home** | Event/gacha popup covers the village → next "Attack" tap hits popup content instead of the button. | Upgrade check runs on wrong screen, may click random popup buttons. |
| 3 | **Attack screen entry** | If search ended abnormally (Shop, popup), attack deploys troops on wrong screen. | Wastes time, may misclick dangerous UI elements. |

## Design

### Architecture: three self-contained verifiers

Each verifier is a standalone async method on `SequenceRunner` with a single responsibility: verify one screen transition and recover if wrong. They are called at the specific insertion points identified below.

```
verify_search_screen()   →  called inside _do_search loop, before each Next tap
verify_home_screen()     →  called at end of _do_return_home
verify_attack_screen()   →  called at start of _do_attack
```

### Templates needed

User must capture these from the emulator and save to `storage/templates/`:

| File | What to capture | Size guidance |
|------|----------------|---------------|
| `icon_shop_tab.png` | The hammer-and-saw tab icon in the Shop screen's top bar | ~40x40px, crop tightly around the icon |
| `btn_shop_close.png` | The X/close button on the Shop screen | ~30x30px, crop tightly |
| `btn_attack.png` | The Attack button on the home village (bottom-left) | ~80x60px, include some padding |

### Verifier 1: Search Screen (Shop detection)

**Insertion point**: `_do_search`, **before** each Next tap (between current line 230 and 231).

**Logic**:
1. Take screenshot
2. Template match `icon_shop_tab.png` (threshold 0.6)
3. If Shop detected:
   - Log warning: "Shop detected during search — closing"
   - Template match `btn_shop_close.png` (threshold 0.5) → tap center + offset
   - If close button not found, fallback: tap at hardcoded position near top-right of Shop (e.g. x=1180, y=80) with large sigma for safety
   - Wait 1-2s for transition
   - Continue search loop
4. If Shop NOT detected → proceed with Next tap as usual

**Why before Next tap, not after**: A misclick happens ON the Next tap, so we can't detect it before that tap. The detection catches the Shop from the PREVIOUS iteration's misclick. First iteration has no verification (acceptable — very unlikely to start the loop already in Shop).

**Performance**: One template match (~2ms) per search iteration. Negligible overhead.

### Verifier 2: Home Screen (Popup dismissal)

**Insertion point**: `_do_return_home`, **after** the blind wait (after current line 1037), **before** the attack log write (current line 1039).

**Logic**:
1. Take screenshot
2. Template match `btn_attack.png` (threshold 0.6)
3. If attack button IS visible → home screen confirmed, proceed normally
4. If attack button NOT visible → a popup is covering the screen:
   - Log warning: "Home screen blocked by popup — dismissing"
   - Enter dismissal loop (max 10 iterations):
     a. Tap at random position on screen (x=400..900, y=300..600, sigma=30)
     b. Wait 1-2s
     c. Take screenshot, check `btn_attack.png`
     d. If found → exit loop, proceed
   - If still not found after 10 taps:
     - Log error: "Failed to dismiss popup after 10 attempts"
     - Use hardcoded home-button fallback (current cx=640, cy=650) and retry verification once
     - If still fails → accept and continue (better than hanging)

**Why random taps for popups**: Event popups vary — some close on background tap, some need specific buttons. Random screen taps are the most universal dismissal method since most CoC popups dismiss on any tap outside their content area.

### Verifier 3: Attack Screen

**Insertion point**: `_do_attack`, **at the very start** of the method (before current line 371).

**Logic**:
1. Take screenshot
2. Run `_detect_cards(adb)` (existing method, already detects troop bar)
3. If cards detected → we are on the attack/battle screen → proceed with normal deploy flow
4. If NO cards detected → we are on the wrong screen:
   - Log warning: "Not on attack screen — returning home and retrying step"
   - Raise a custom `ScreenVerificationError` exception
   - This exception is caught by the step-level error handler (see Step-Level Recovery below)

**Why exception instead of inline recovery**: The attack screen failure means the farming step got completely derailed (Shop, popup, wrong screen). The step should be retried from the beginning. An exception lets the step retry handler decide: retry the attack step, or return home and restart the loop.

### Step-Level Recovery

**Insertion point**: `_run`, the `except Exception` block (current lines 130-132).

**Current behavior**: Log error, sleep 2s, continue to next step.

**New behavior**:
```
step fails → retry once → if still fails → return_home → skip to next step
```

Specific logic:
1. First failure of a step type: log warning, wait 1s, retry the exact same step
2. Second failure: log error, execute `_do_return_home`, then `continue` to next step
3. `ScreenVerificationError` from verifier 3: treat as step failure, triggering the retry/recovery above

Implementation detail: add a `_step_retry_count` dict (keyed by step index) that resets at the start of each loop iteration. Or simpler: use a single `_last_failed_step_idx` and reset it on success:

```python
_last_failed_step_idx = -1

for i, step in enumerate(steps):
    try:
        await self._execute_step(step, adb)
        _last_failed_step_idx = -1  # success resets
    except Exception as e:
        if i == _last_failed_step_idx:
            # same step failed twice — go home and skip
            logger.error("Step %s failed twice — returning home", step.step_type)
            await self._do_return_home(adb)
            _last_failed_step_idx = -1
            continue
        else:
            # first failure — retry
            logger.warning("Step %s failed — retrying once", step.step_type)
            _last_failed_step_idx = i
            # repeat the step by decrementing loop index
            # (need to restructure from for-loop to while-with-index)
```

### Retry loop restructuring

The current `for step in steps` must become `while i < len(steps)` to support step retry:

```python
i = 0
while i < len(steps) and self._running:
    step = steps[i]
    try:
        await self._execute_step(step, adb)
        _last_failed_step_idx = -1
        i += 1
    except ScreenVerificationError:
        _handle_step_failure(step, i)
    except Exception as e:
        logger.error("Step %s failed: %s", step.step_type, e)
        _handle_step_failure(step, i)
```

### New Dependencies

None. All features use existing infrastructure:
- `match_template()` from `backend/vision/matching.py` — already imported
- `human_tap()` from `backend/humanize/__init__.py` — already imported
- `adb.screencap()` — already available
- `_detect_cards()` — already exists in SequenceRunner
- `_do_return_home()` — already exists

### Files Changed

| File | Change |
|------|--------|
| `backend/engine/sequence_runner.py` | Add 3 verifier methods + modify `_do_search`, `_do_return_home`, `_do_attack`, `_run` |
| `storage/templates/` | User adds 3 PNG templates |

No frontend changes needed. Verification failures are logged and recovered autonomously.

### Error Logging Strategy

All verification failures log at WARNING level with clear messages:
- `"Shop detected during search — closing"` (verifier 1)
- `"Home screen blocked by popup — dismissing (attempt X/10)"` (verifier 2)
- `"Not on attack screen — recovery triggered"` (verifier 3)

These are visible in the Logs tab and provide clear diagnostics.

### What this does NOT handle

- **Mid-battle Shop clicks**: Not in scope. The battle deploy flow doesn't navigate UI that could lead to Shop.
- **Network disconnects**: Not in scope. ADB reconnect is handled elsewhere.
- **Game crash/restart**: Not in scope. Handled by the existing `_running` flag checks throughout the loop.
- **Calibration drift**: Not in scope. If calibration is fundamentally wrong, no recovery can fix it.
