# Dual Sequence Auto-Switch — Design Spec

**Status:** Draft
**Date:** 2026-08-01

## Problem

Current bot has one sequence ("Attack Loop") that mixes farming and upgrade steps in a single linear flow:
```
attack → return_home → upgrade_check → upgrade_execute → repeat
```
This has several issues:

1. **Upgrade tied to attack** — upgrade runs every cycle regardless of whether resources are sufficient
2. **UpgradeQueue complexity** — user must manually maintain a priority queue of building names and target levels
3. **AI underused** — AI vision can detect upgradable buildings directly from the builder menu, no queue needed
4. **No smart switching** — bot doesn't know whether to farm or upgrade based on game state

## Goal

Two separate bot sequences — **Farming** and **Upgrade** — that auto-switch based on in-game conditions:

- **Farming → Upgrade**: when at least 1 builder is free AND resources are sufficient for the cheapest suggested upgrade
- **Upgrade → Farming**: when builders reach 0 OR resources are insufficient for any upgrade

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ SequenceRunner._run()                                       │
│                                                             │
│   while _running:                                           │
│       mode = await _evaluate_mode(adb)                      │
│       steps = farming_steps if mode == "farming"            │
│                else upgrade_steps                           │
│       for each step: _execute_step(step, adb)               │
│       # re-evaluate mode at end of each loop pass           │
└─────────────────────────────────────────────────────────────┘

_evaluate_mode():
  1. Screencap
  2. OCR builder_count ROI
  3. If builder == 0 → "farming"
  4. Tap builder_menu → screencap → AI analyze suggested list
  5. Get cheapest suggested building (top of list)
  6. OCR gold/elixir/DE
  7. If resources[building.resource] >= building.cost → "upgrade"
  8. Else → "farming"
```

## Components

### 1. Two DB Sequences

| Name | Steps | Active |
|------|-------|--------|
| **Farming Loop** | tap(btn_attack) → wait(3s) → tap(btn_find_match) → wait(6s) → tap(myarmy_btn_attack) → wait(3s) → search({max_searches:30}) → attack({strategy:"4finger",duration:180}) → return_home | yes (default) |
| **Upgrade Loop** | upgrade_check → upgrade_execute → return_home | no |

The `return_home` step in Upgrade Loop is a safety net — dismisses any unexpected dialogs and ensures clean state.

### 2. `_evaluate_mode(adb)` — NEW

Called at the start of each loop pass to determine which sequence to run.

```
Returns: "farming" | "upgrade"

Logic:
  1. OCR builder_count from ROI — if 0, return "farming"
  2. Tap builder_menu, screencap
  3. Send to DashScope AI with prompt asking for list of suggested upgrades
     (same AI client, different prompt — focuses on cheapest/available)
  4. Parse AI response → get top suggested building
  5. OCR current resources (gold, elixir, DE)
  6. Compare resource of suggested building vs current
  7. If sufficient AND builder > 0 → "upgrade"
  8. Else → "farming"
```

Error handling: if any step fails (builder menu ROI not found, AI unavailable, OCR fails), default to "farming". Never get stuck.

### 3. `_do_upgrade_check` — SIMPLIFIED

```
Old: read UpgradeQueue → find PENDING items → check resources → set _upgrade_item
New:  tap builder_menu → screencap → AI analyze → get cheapest →
      check resources → set _upgrade_target (dict with {name, x, y, cost, resource})
```

No more `UpgradeQueue` dependency for check. AI directly identifies what to upgrade.

### 4. `_do_upgrade_execute` — MINOR CHANGE

Rename `self._upgrade_item` → `self._upgrade_target`. Everything else stays the same — AI tap building coords, template match confirm button, update DB.

Wait — no more DB update for UpgradeQueue. The upgrade flow should only update internal state, not a queue table. Simplify: just log the upgrade, no DB write needed for queue.

### 5. Sequence Seeding (`init_db`)

- Rename existing "Attack Loop" to "Farming Loop"
- Remove `upgrade_check` and `upgrade_execute` steps from the farming sequence
- Add new "Upgrade Loop" sequence with 3 steps
- Migration shim: update existing "Attack Loop" name + remove upgrade steps if present

### 6. Frontend Updates

- Sequence selector already supports multiple sequences — no change needed
- Sequence editor already supports all step types — no change needed
- Builder tab (`UpgradeQueue`) can be hidden or repurposed later — out of scope for now

## Data Flow

```
Loop Start
    │
    ▼
_evaluate_mode(adb)
    │
    ├── "farming"
    │       │
    │       ▼
    │   run Farming Loop steps
    │
    └── "upgrade"
            │
            ▼
        run Upgrade Loop steps
            │
            ├── upgrade_check: builder_menu → AI → cheapest → resource OK? → _upgrade_target
            ├── upgrade_execute: tap _upgrade_target coords → confirm
            └── return_home: safety tap
    │
    ▼
Loop End → re-evaluate mode
```

## Error Handling

| Scenario | Handling |
|----------|----------|
| builder_count ROI not found | Default builder_count = 0 → farm |
| AI unavailable during evaluate | Default to "farming" |
| AI unavailable during upgrade_check | Log, skip this pass |
| OCR resources fails | Default to 0 → farm |
| builder_menu ROI not found | Default to "farming" |
| Confirm button not found after upgrade tap | Skip, try next pass |

## Out of Scope

- Removing Builder tab UI from frontend (kept for now)
- Removing `UpgradeQueue` model/API (kept but unused by upgrade flow)
- Multiple upgrades per pass (one per pass)
- Lab/hero upgrades
- Resource "full" detection (storage capacity check)

## Files Changed

| File | Action | Description |
|------|--------|-------------|
| `backend/engine/sequence_runner.py` | **EDIT** | `_run()` dual-mode, `_evaluate_mode()`, simplify upgrade methods, rename `_upgrade_item` → `_upgrade_target` |
| `backend/db/database.py` | **EDIT** | Seed two sequences, remove upgrade steps from farming |
