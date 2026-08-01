# Auto-Upgrade Priority Queue — Design Spec

**Date**: 2026-08-01
**Status**: Draft
**Goal**: Full-auto upgrade system: bot attacks to farm resources, checks a priority queue, and automatically upgrades buildings when resources and builders are available.

## Architecture

```
MAIN LOOP
  ┌──────────┐     ┌──────────────┐     ┌────────────┐
  │  ATTACK  │────→│ CHECK QUEUE  │────→│  UPGRADE   │
  │  LOOP    │     │ (resources)  │     │ (if ready) │
  └──────────┘     └──────────────┘     └────────────┘
       ↑                  │                   │
       │   resources      │  affordable +     │
       │   NOT enough     │  builder free     │
       └──────────────────┘                   │
               ┌──────────────────────────────┘
               │ (after upgrade, re-check queue)
```

**Per-iteration logic**:
1. Attack loop → earn gold/elixir/dark elixir
2. Scan upgrade queue from highest priority
3. For each PENDING item: check resource sufficiency + builder availability
4. If upgrade is possible → navigate to building → OCR cost → confirm upgrade
5. If not → try next item
6. If nothing can be upgraded → resume attack loop

## Data Model

### UpgradeQueue (extend existing)

| Field | Type | Description |
|-------|------|-------------|
| `id` | int (PK) | Auto-increment |
| `name` | str(64) | "Archer Tower", "Wizard Tower" |
| `target_level` | int | Target upgrade level |
| `resource_type` | str | "gold", "elixir", "dark_elixir" |
| `upgrade_type` | str | "building" (lab deferred to v2) |
| `cost` | int | Auto-detected from upgrade screen via OCR |
| `priority_order` | int | Lower number = higher priority |
| `status` | str | PENDING, IN_PROGRESS, COMPLETED |
| `started_at` | datetime | When upgrade began |
| `completed_at` | datetime | When upgrade finished |

**Note**: `cost` starts empty. Bot fills it when checking the upgrade screen. This allows the bot to detect actual cost (which varies by level/town hall discounts).

### Config (UPGRADE category, optional)

| Key | Value | Description |
|-----|-------|-------------|
| `builder_count` | 5 | Default available builders |

## Sequence Step Types

### New step types

| Step | Handler | Function |
|------|---------|----------|
| `upgrade_check` | `_do_upgrade_check()` | Scan queue → check resources → check builders → decide |
| `upgrade_execute` | `_do_upgrade_execute()` | Navigate to building → OCR cost → tap upgrade → confirm |

### Updated attack loop sequence

```
tap btn_attack
wait
tap btn_find_match
wait
tap army_btn_attack
wait
search
attack
return_home
upgrade_check      # NEW: check if any upgrade can be done
upgrade_execute    # NEW: perform upgrade if check says READY
```

### Return values

`upgrade_check` returns status:
- `UPGRADE_READY` → proceed to `upgrade_execute`
- `UPGRADE_NOT_READY` → skip `upgrade_execute`, restart attack loop

## Execution Flow

### `upgrade_check` — Check if upgrade is possible

```
1. Query UpgradeQueue: PENDING building items, ordered by priority_order
2. If no PENDING items → return UPGRADE_NOT_READY
3. For each item:
   a. Read current gold/elixir/DE via read_number() on gold_number/elixir_number/de_number ROIs
   b. If item.cost > 0 (previously detected):
      - If current_resource < item.cost → SKIP, try next
   c. If item.cost == 0 (first time):
      - Always proceed (will OCR actual cost in upgrade_execute)
   d. Check builder availability:
      - Tap to show builder panel (or read builder_count ROI)
      - OCR builder count: "N/5" or similar
      - If free_builders < 1 → SKIP all building items → UPGRADE_NOT_READY
   e. Upgrade possible! → store selected_item in state → return UPGRADE_READY
4. If all items skipped → return UPGRADE_NOT_READY
```

### `upgrade_execute` — Perform the upgrade

```
1. Get selected_item from upgrade_check state
2. Open builder menu / find building:
   - Use calibrated ROI for the building position OR
   - Use a general "builder suggestion" dropdown
3. Tap the building to upgrade
4. Read upgrade cost via OCR:
   - ROI calibration: "upgrade_cost" read region on upgrade screen
   - read_number() to get actual cost
5. Compare cost against current resources:
   - If NOT affordable → return UPGRADE_NOT_READY (unexpected, log warning)
   - If affordable → continue
6. Tap "Upgrade" button (calibrated ROI: "btn_upgrade_confirm")
7. Confirm upgrade (calibrated ROI: "btn_upgrade_confirm_final")
8. Update item in DB: status=IN_PROGRESS, cost=<detected>, started_at=now
9. Return UPGRADE_READY (to re-check for next item)
```

## Builder Detection

### Approach: OCR builder count

- Use `read_number()` to read the builder count display
- ROI calibration: `builder_count` read region
- Parse format: "N" (free) from "N/M" display
- If free > 0 → building upgrade possible
- If free == 0 → skip all building upgrades

### Calibration ROIs needed

| ROI Name | Type | Purpose |
|----------|------|---------|
| `builder_count` | read | OCR builder free/total number |
| `builder_menu` | tap | Location to show builder/upgrade panel |
| `btn_upgrade` | tap | "Upgrade" button on building info panel |
| `btn_upgrade_confirm` | tap | Confirm upgrade (second tap) |
| `upgrade_cost` | read | OCR region for upgrade resource cost |

## Frontend

### Builder Tab

- **Upgrade Queue list** — table showing name, target level, resource, priority, status
- **Add item** — form: name, target_level, resource_type, priority (auto-increment)
- **Delete item** — X button per row
- **Reorder** — up/down arrows to change priority
- **Status badges** — color-coded: PENDING (yellow), IN_PROGRESS (blue), COMPLETED (green)
- **Builder status** — live builder count from WebSocket

### Sequence Editor

- Add `upgrade_check` and `upgrade_execute` to step type dropdown

## REST API

### New: `backend/api/rest_upgrade.py`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/upgrade/queue` | List all queue items (ordered by priority) |
| `POST` | `/api/v1/upgrade/queue` | Add item (name, target_level, resource_type, priority) |
| `PUT` | `/api/v1/upgrade/queue/{id}` | Update item (reorder, edit fields) |
| `DELETE` | `/api/v1/upgrade/queue/{id}` | Remove item |
| `PATCH` | `/api/v1/upgrade/queue/{id}/status` | Update status (PENDING→IN_PROGRESS→COMPLETED) |
| `GET` | `/api/v1/upgrade/status` | Current upgrade queue status + builder count |

### Modified: `backend/api/rest_sequence.py`

- Add `upgrade_check` and `upgrade_execute` to valid step types

## Files Changed

| File | Change |
|------|--------|
| `backend/db/models.py` | Add `resource_type`, `upgrade_type`, `cost` to `UpgradeQueue` |
| `backend/api/rest_upgrade.py` | **NEW** — CRUD endpoints |
| `backend/api/rest_sequence.py` | Add new step types |
| `backend/engine/sequence_runner.py` | Add `_do_upgrade_check()`, `_do_upgrade_execute()` |
| `backend/main.py` | Register `rest_upgrade` router |
| `frontend/index.html` | Wire builder tab: queue list, add/remove/reorder |
| `frontend/src/main.js` | Alpine state + fetch calls for upgrade queue |

## Out of Scope (v2)

- Laboratory upgrades (troops/spells/vehicles)
- Auto-detect building position on screen (v1 uses manually calibrated ROIs)
- Auto-recovery if upgrade fails mid-way
- Dark elixir hero upgrades
- Upgrade cost history / tracking
