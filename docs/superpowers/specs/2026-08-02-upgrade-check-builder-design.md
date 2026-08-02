# Builder Check in upgrade_check — Design Spec

**Date**: 2026-08-02
**Status**: Approved
**Goal**: Add builder availability check at the start of `upgrade_check` step so the bot skips upgrade when no builders are free.

## Problem

`_do_upgrade_check()` currently only reads resources and stores them in `_upgrade_target`. It does not verify that a builder is actually available. If builders = 0, the bot still proceeds to `upgrade_execute` which wastes time with AI calls and template matching before eventually failing.

`_read_builder_count()` already exists (line 468) — it just isn't called during upgrade_check.

## Design

### Modified method: `_do_upgrade_check()` (`sequence_runner.py:402-413`)

New flow:
1. Screenshot
2. Read builder count via `_read_builder_count(screen)`
3. If < 1 → log, set `_upgrade_target = None`, return
4. If >= 1 → read resources as before, store in `_upgrade_target`

```python
async def _do_upgrade_check(self, adb):
    """Read builder count and resources, store for _do_upgrade_execute."""
    self.state = "UPGRADE_CHECK"
    logger.info("Checking builders and resources for upgrade...")
    screen = await adb.screencap()
    if not screen:
        self._upgrade_target = None
        return

    builders = self._read_builder_count(screen)
    if builders < 1:
        logger.info("No free builders — skipping upgrade")
        self._upgrade_target = None
        return

    resources = self._read_resources(screen)
    logger.info("Builders=%d Resources: G=%d E=%d DE=%d",
                 builders, resources["gold"], resources["elixir"], resources["dark_elixir"])
    self._upgrade_target = {"resources": resources}
```

### No changes needed in other methods

- **`_do_upgrade_execute()`** (line 581): already checks `_upgrade_target is None` at line 587 and returns early.
- **`_evaluate_mode()`** (line 484): already checks builders independently and returns "farming" when < 1.

### Files Changed

| File | Change |
|---|---|
| `backend/engine/sequence_runner.py` | Modify `_do_upgrade_check()` — add builder count check before resource read |
