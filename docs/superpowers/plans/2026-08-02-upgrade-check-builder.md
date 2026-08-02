# Builder Check in upgrade_check — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add builder availability check at the start of `_do_upgrade_check()` so upgrade is skipped when no builders are free.

**Architecture:** Modify `_do_upgrade_check()` to call the existing `_read_builder_count()` before reading resources. If builders < 1, set `_upgrade_target = None` and return early. No other methods need changes.

**Tech Stack:** Python (asyncio, PIL/Pillow)

---

### Task 1: Add builder check to _do_upgrade_check()

**Files:**
- Modify: `backend/engine/sequence_runner.py:402-413`

- [ ] **Step 1: Replace _do_upgrade_check() method**

Read the current method at lines 402-413 of `C:\programming\python\backend\engine\sequence_runner.py`. Replace the entire method with:

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

- [ ] **Step 2: Verify Python syntax**

```bash
python -c "import ast; ast.parse(open('backend/engine/sequence_runner.py').read()); print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/engine/sequence_runner.py
git commit -m "feat: add builder availability check to upgrade_check"
```
