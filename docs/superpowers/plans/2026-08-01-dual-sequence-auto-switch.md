# Dual Sequence Auto-Switch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single "Attack Loop" sequence with two sequences (Farming Loop + Upgrade Loop) that auto-switch based on builder count and resource availability. Simplify upgrade flow to use AI directly, no UpgradeQueue.

**Architecture:** Modify `_run()` in SequenceRunner to call `_evaluate_mode()` at each loop pass — checks builder count + AI-suggested cheapest upgrade vs resources. Seed two sequences in `init_db()`. Simplify `_do_upgrade_check` to use AI instead of UpgradeQueue.

**Tech Stack:** Python 3.12, FastAPI, SQLModel, dashscope, OpenCV, EasyOCR

---

### Task 1: Seed two sequences in init_db

**Files:**
- Modify: `backend/db/database.py:62-108`

- [ ] **Step 1: Replace sequence seeding**

Replace the entire seed block (lines 62-108) — the Attack Loop creation + migration shim — with dual-sequence seeding:

```python
    # Seed default sequences
    from backend.db.models import BotSequence, SequenceStep

    farming_steps = [
        ("tap", 0, "btn_attack", None, None),
        ("wait", 1, None, 3.0, None),
        ("tap", 2, "btn_find_match", None, None),
        ("wait", 3, None, 6.0, None),
        ("tap", 4, "myarmy_btn_attack", None, None),
        ("wait", 5, None, 3.0, None),
        ("search", 6, None, None, '{"max_searches":30}'),
        ("attack", 7, None, None, '{"strategy":"4finger","duration":180}'),
        ("return_home", 8, None, None, None),
    ]

    upgrade_steps = [
        ("upgrade_check", 0, None, None, None),
        ("upgrade_execute", 1, None, None, None),
        ("return_home", 2, None, None, None),
    ]

    with Session(engine) as session:
        # --- Farming Loop ---
        existing = session.exec(select(BotSequence).where(
            BotSequence.name == "Farming Loop")).first()
        if not existing:
            # Remove old "Attack Loop" if it exists
            old = session.exec(select(BotSequence).where(
                BotSequence.name == "Attack Loop")).first()
            if old:
                # Delete old steps
                old_steps = session.exec(select(SequenceStep).where(
                    SequenceStep.sequence_id == old.id)).all()
                for s in old_steps:
                    session.delete(s)
                session.delete(old)
                session.commit()

            seq = BotSequence(name="Farming Loop",
                              description="Full attack farming cycle", is_active=True)
            session.add(seq)
            session.commit()
            session.refresh(seq)
            for stype, order, roi, dur, cfg in farming_steps:
                session.add(SequenceStep(
                    sequence_id=seq.id, step_order=order,
                    step_type=stype, roi_name=roi,
                    duration=dur, config_json=cfg,
                ))
            session.commit()

        # --- Upgrade Loop ---
        existing = session.exec(select(BotSequence).where(
            BotSequence.name == "Upgrade Loop")).first()
        if not existing:
            seq = BotSequence(name="Upgrade Loop",
                              description="Auto-upgrade cheapest suggested building",
                              is_active=False)
            session.add(seq)
            session.commit()
            session.refresh(seq)
            for stype, order, roi, dur, cfg in upgrade_steps:
                session.add(SequenceStep(
                    sequence_id=seq.id, step_order=order,
                    step_type=stype, roi_name=roi,
                    duration=dur, config_json=cfg,
                ))
            session.commit()
```

- [ ] **Step 2: Rebuild DB and verify**

Run: `python -c "from backend.db.database import init_db; init_db(); print('DB rebuilt')"`
Expected: `DB rebuilt`

Then verify the sequences exist:
```bash
python -c "from backend.db.database import get_session; from backend.db.models import BotSequence, SequenceStep; s=get_session(); ctx=s(); seqs=ctx.exec(__import__('sqlmodel').select(BotSequence)).all(); [print(f'{sq.name} active={sq.is_active}') for sq in seqs]; ctx.close()"
```
Expected output shows both "Farming Loop" (active=True) and "Upgrade Loop" (active=False)

- [ ] **Step 3: Commit**

```bash
git add backend/db/database.py
git commit -m "feat: seed dual sequences (Farming + Upgrade) replacing Attack Loop

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 2: Rewire sequence_runner for dual mode

**Files:**
- Modify: `backend/engine/sequence_runner.py`

- [ ] **Step 1: Rename _upgrade_item to _upgrade_target**

In `__init__()` line 30, rename the attribute:

```python
        self._upgrade_target = None  # {name, x, y, cost, resource} from AI
```

Find and replace ALL occurrences of `_upgrade_item` with `_upgrade_target` in the file. The grep pattern: search for `self._upgrade_item` and replace with `self._upgrade_target`. There are usages in: `__init__`, `_do_upgrade_check`, `_do_upgrade_execute`, and `_do_upgrade_execute_template`.

In `_do_upgrade_check`, also replace `item.name` / `item.target_level` references — these come from the UpgradeQueue model and should become `building["name"]` / `building["resource"]` etc.

- [ ] **Step 2: Add _read_resources helper**

Add a helper method before `_do_upgrade_check` (after `_TPL_CONFIRM`, around line 452):

```python
    def _read_resources(self, screen) -> dict:
        """OCR gold, elixir, and dark elixir from a screenshot. Returns {'gold': N, 'elixir': N, 'dark_elixir': N}."""
        from backend.vision.ocr import read_number
        with get_session() as session:
            gold_roi = session.query(RoiTemplate).filter_by(roi_name="gold_number").first()
            elixir_roi = session.query(RoiTemplate).filter_by(roi_name="elixir_number").first()
            de_roi = session.query(RoiTemplate).filter_by(roi_name="dark_elixir_number").first()
        gold = gold_roi and read_number(screen, gold_roi.x_pos, gold_roi.y_pos,
                                        gold_roi.width, gold_roi.height, roi_name=gold_roi.roi_name)
        elixir = elixir_roi and read_number(screen, elixir_roi.x_pos, elixir_roi.y_pos,
                                            elixir_roi.width, elixir_roi.height, roi_name=elixir_roi.roi_name)
        de = de_roi and read_number(screen, de_roi.x_pos, de_roi.y_pos,
                                    de_roi.width, de_roi.height, roi_name=de_roi.roi_name)
        return {"gold": gold or 0, "elixir": elixir or 0, "dark_elixir": de or 0}

    def _read_builder_count(self, screen) -> int:
        """OCR free builder count from a screenshot. Returns 0 if OCR fails."""
        from backend.vision.ocr import read_number
        with get_session() as session:
            builder_roi = session.query(RoiTemplate).filter_by(roi_name="builder_count").first()
        if not builder_roi:
            return 0
        bc = read_number(screen, builder_roi.x_pos, builder_roi.y_pos,
                        builder_roi.width, builder_roi.height, roi_name="builder_count")
        return bc if bc is not None else 0
```

- [ ] **Step 3: Add _evaluate_mode method**

Insert after `_read_builder_count()`:

```python
    async def _evaluate_mode(self, adb) -> str:
        """Determine whether to farm or upgrade. Returns 'farming' or 'upgrade'."""
        screen = await adb.screencap()
        if not screen:
            return "farming"

        # Check builder count
        builders = self._read_builder_count(screen)
        if builders < 1:
            logger.info("No free builders — farming mode")
            return "farming"

        # Try AI to find cheapest suggested upgrade
        client = self._get_ai_client()
        if not client.available:
            logger.info("AI not available — farming mode")
            return "farming"

        # Compare with resources
        resources = self._read_resources(screen)
        logger.info("Resources: G=%d E=%d DE=%d, builders=%d",
                     resources["gold"], resources["elixir"],
                     resources["dark_elixir"], builders)

        # Tap builder menu to check suggested upgrades
        with get_session() as session:
            menu_roi = session.query(RoiTemplate).filter_by(roi_name="builder_menu").first()
        if not menu_roi:
            return "farming"
        menu_cx = menu_roi.x_pos + menu_roi.width // 2
        menu_cy = menu_roi.y_pos + menu_roi.height // 2
        await human_tap(adb, menu_cx, menu_cy, sigma=3)
        await human_delay(1.5, 2.5)

        screen2 = await adb.screencap()
        if not screen2:
            await human_tap(adb, menu_cx, menu_cy, sigma=3)  # close
            return "farming"

        ai_buildings = client.analyze_screenshot(screen2)
        # Close builder menu
        await human_tap(adb, menu_cx, menu_cy, sigma=3)
        await human_delay(0.3, 0.8)

        if not ai_buildings:
            logger.info("No upgradable buildings found — farming mode")
            return "farming"

        cheapest = ai_buildings[0]  # top of suggested list
        res_val = resources.get(cheapest.get("resource", "gold"), 0)
        cost = cheapest.get("cost", 0)
        if res_val >= cost and cost > 0:
            logger.info("Affordable upgrade: %s (%d %s) — upgrade mode",
                         cheapest["name"], cost, cheapest["resource"])
            return "upgrade"

        logger.info("Cannot afford %s (need %d %s, have %d) — farming mode",
                     cheapest["name"], cost, cheapest["resource"], res_val)
        return "farming"
```

- [ ] **Step 4: Simplify _do_upgrade_check**

Replace the entire `_do_upgrade_check` method (lines 369-445) with the AI-based version:

```python
    async def _do_upgrade_check(self, adb):
        """Check builder menu via AI for cheapest upgradable building."""
        from backend.db.database import get_session
        from backend.db.models import RoiTemplate

        self.state = "UPGRADE_CHECK"
        logger.info("Checking for upgrades...")

        self._upgrade_target = None

        # Tap builder menu
        with get_session() as session:
            menu_roi = session.query(RoiTemplate).filter_by(roi_name="builder_menu").first()
        if not menu_roi:
            logger.warning("builder_menu ROI not calibrated")
            return
        menu_cx = menu_roi.x_pos + menu_roi.width // 2
        menu_cy = menu_roi.y_pos + menu_roi.height // 2
        await human_tap(adb, menu_cx, menu_cy, sigma=3)
        await human_delay(1.5, 2.5)

        screen = await adb.screencap()
        if not screen:
            return

        client = self._get_ai_client()
        if not client.available:
            logger.warning("AI unavailable — cannot check upgrades")
            await human_tap(adb, menu_cx, menu_cy, sigma=3)
            await human_delay(0.3, 0.8)
            return

        ai_buildings = client.analyze_screenshot(screen)
        if not ai_buildings:
            logger.info("No upgradable buildings found")
            await human_tap(adb, menu_cx, menu_cy, sigma=3)
            await human_delay(0.3, 0.8)
            return

        building = ai_buildings[0]  # cheapest (top of suggested list)
        logger.info("Suggested: %s (%d %s)",
                     building["name"], building["cost"], building["resource"])

        # Check resources
        resources = self._read_resources(screen)
        res_val = resources.get(building.get("resource", "gold"), 0)
        if res_val < building.get("cost", 0):
            logger.info("Cannot afford %s (need %d %s, have %d)",
                         building["name"], building["cost"],
                         building["resource"], res_val)
            await human_tap(adb, menu_cx, menu_cy, sigma=3)
            await human_delay(0.3, 0.8)
            return

        # Keep builder menu open — _do_upgrade_execute will use the coordinates
        self._upgrade_target = building
        logger.info("Ready to upgrade: %s (coords: %d,%d)",
                     building["name"], building["x"], building["y"])
```

- [ ] **Step 5: Simplify _do_upgrade_execute**

Replace the entire `_do_upgrade_execute` method (lines 490-592) — builder menu is already open from `_do_upgrade_check`, so just tap the stored coordinates:

```python
    async def _do_upgrade_execute(self, adb):
        """Execute upgrade using coordinates from _do_upgrade_check (menu already open)."""
        from backend.vision.matching import match_template

        if not getattr(self, "_upgrade_target", None):
            logger.info("No upgrade target — skipping")
            return

        building = self._upgrade_target
        self.state = "UPGRADING"
        logger.info("Upgrading: %s at (%d,%d) cost=%d %s",
                     building["name"], building["x"], building["y"],
                     building["cost"], building["resource"])

        # Step 1: Tap upgrade button at AI coordinates (menu already open from check)
        await human_tap(adb, building["x"], building["y"], sigma=3)
        await human_delay(0.8, 1.5)

        # Step 2: Find and tap confirm button
        screen = await adb.screencap()
        if not screen:
            return
        confirm_pos = None
        for tpl_path in self._TPL_CONFIRM:
            confirm_pos = match_template(screen, tpl_path, threshold=0.7)
            if confirm_pos:
                break
        if confirm_pos:
            await human_tap(adb, confirm_pos[0], confirm_pos[1], sigma=3)
            await human_delay(0.5, 1.0)
        else:
            logger.warning("Confirm button not found")
            self._upgrade_target = None
            return

        logger.info("Upgrade started: %s (cost=%d %s)",
                     building["name"], building["cost"], building["resource"])
        self._upgrade_target = None
        await human_delay(1.0, 2.0)
```

Also update `_do_upgrade_execute_template` — replace every `self._upgrade_item` with `self._upgrade_target` and every `item = self._upgrade_item` with `building = self._upgrade_target`. Update `item.name`/`item.target_level`/`item.id` references to use `building` dict keys. Remove the DB update block (lines ~688-705) and replace with:

```python
        logger.info("Template upgrade: %s (cost=%d)",
                     building["name"], detected_cost or 0)
        self._upgrade_target = None
        await human_delay(1.0, 2.0)
```

- [ ] **Step 6: Modify _run() for dual-mode**

Replace `_run()` (lines 63-88) with dual-mode version:

```python
    async def _run(self, sequence_id: int | None = None):
        adb = adb_manager
        if not adb.is_connected:
            await adb.connect()

        # Load both sequences
        with get_session() as session:
            from backend.db.models import BotSequence, SequenceStep

            farming_seq = session.exec(
                select(BotSequence).where(BotSequence.name == "Farming Loop")
            ).first()
            upgrade_seq = session.exec(
                select(BotSequence).where(BotSequence.name == "Upgrade Loop")
            ).first()

            if not farming_seq or not upgrade_seq:
                logger.error("Required sequences not found (Farming Loop + Upgrade Loop)")
                return

            farming_steps = session.exec(
                select(SequenceStep).where(SequenceStep.sequence_id == farming_seq.id)
                .order_by(SequenceStep.step_order)
            ).all()
            upgrade_steps = session.exec(
                select(SequenceStep).where(SequenceStep.sequence_id == upgrade_seq.id)
                .order_by(SequenceStep.step_order)
            ).all()

        current_mode = "farming"

        while self._running:
            self.state = "RUNNING"
            steps = farming_steps if current_mode == "farming" else upgrade_steps

            for step in steps:
                if not self._running:
                    break
                try:
                    await self._execute_step(step, adb)
                except Exception as e:
                    logger.error("Step %s failed: %s", step.step_type, e)
                    await asyncio.sleep(2)

            # Re-evaluate mode for next pass
            if self._running:
                current_mode = await self._evaluate_mode(adb)
```

- [ ] **Step 7: Verify syntax and import**

Run: `python -c "from backend.engine.sequence_runner import SequenceRunner; print('OK')"`
Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add backend/engine/sequence_runner.py
git commit -m "feat: dual-mode farming/upgrade with auto-switch and AI-driven upgrade

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 3: Final verification

**Files:**
- No file changes, verification only

- [ ] **Step 1: Rebuild DB**

Run: `python -c "from backend.db.database import init_db; init_db(); print('DB OK')"`
Expected: `DB OK`

- [ ] **Step 2: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 3: Verify full import chain**

Run: `python -c "from backend.main import app; print('Full import OK')"`
Expected: `Full import OK`

- [ ] **Step 4: Commit final state**

```bash
git status
git commit -m "chore: final verification — dual sequence auto-switch ready

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```
