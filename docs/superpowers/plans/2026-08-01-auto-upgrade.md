# Auto-Upgrade Priority Queue — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Full-auto building upgrade system: bot attacks to farm, checks a priority queue, auto-upgrades buildings when resources + builders are available.

**Architecture:** Extend `UpgradeQueue` DB model with resource/cost fields. Add `rest_upgrade.py` CRUD API. Add `upgrade_check`/`upgrade_execute` step types to `SequenceRunner`. Wire builder tab in frontend with Alpine.js.

**Tech Stack:** Python 3.12, FastAPI, SQLModel, OpenCV/NumPy, EasyOCR, Tailwind CSS, Alpine.js

---

### Task 1: Extend UpgradeQueue model

**Files:**
- Modify: `backend/db/models.py:38-46`

- [ ] **Step 1: Add new fields to UpgradeQueue**

Replace lines 38-46 in `backend/db/models.py`:

```python
class UpgradeQueue(SQLModel, table=True):
    __tablename__ = "upgrade_queue"
    id: int = Field(default=None, primary_key=True)
    name: str = Field(default="", max_length=64)
    target_level: int
    resource_type: str = Field(default="gold", max_length=16)  # gold, elixir, dark_elixir
    upgrade_type: str = Field(default="building", max_length=16)  # building (lab v2)
    cost: int | None = Field(default=None)  # auto-detected, None until first check
    priority_order: int
    status: str = Field(default="PENDING", max_length=20)
    started_at: datetime | None = None
    completed_at: datetime | None = None
```

Note: rename `building_name` → `name` for consistency.

- [ ] **Step 2: Rebuild database**

Run: `python -c "from backend.db.database import init_db; init_db(); print('DB rebuilt')"`
Expected: `DB rebuilt`

- [ ] **Step 3: Commit**

```bash
git add backend/db/models.py
git commit -m "feat: extend UpgradeQueue with resource_type, upgrade_type, cost
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 2: Create REST API for upgrade queue

**Files:**
- Create: `backend/api/rest_upgrade.py`

- [ ] **Step 1: Create rest_upgrade.py**

Create `backend/api/rest_upgrade.py`:

```python
"""REST endpoints for upgrade queue management."""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db.database import get_session
from backend.db.models import UpgradeQueue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/upgrade")

# --- Request/Response Models ---

class UpgradeItemCreate(BaseModel):
    name: str
    target_level: int
    resource_type: str = "gold"
    upgrade_type: str = "building"
    priority_order: int | None = None

class UpgradeItemUpdate(BaseModel):
    name: str | None = None
    target_level: int | None = None
    resource_type: str | None = None
    priority_order: int | None = None
    status: str | None = None
    cost: int | None = None

class UpgradeItemResponse(BaseModel):
    id: int
    name: str
    target_level: int
    resource_type: str
    upgrade_type: str
    cost: int | None
    priority_order: int
    status: str
    started_at: datetime | None
    completed_at: datetime | None

# --- Endpoints ---

@router.get("/queue")
async def list_queue():
    """List all queue items ordered by priority."""
    with get_session() as session:
        items = session.query(UpgradeQueue).order_by(UpgradeQueue.priority_order).all()
        return [UpgradeItemResponse(
            id=i.id, name=i.name, target_level=i.target_level,
            resource_type=i.resource_type, upgrade_type=i.upgrade_type,
            cost=i.cost, priority_order=i.priority_order,
            status=i.status, started_at=i.started_at, completed_at=i.completed_at,
        ) for i in items]


@router.post("/queue")
async def create_item(req: UpgradeItemCreate):
    """Add a new item to the upgrade queue."""
    with get_session() as session:
        # Auto-assign priority: one past the highest existing
        if req.priority_order is None:
            highest = session.query(UpgradeQueue).order_by(
                UpgradeQueue.priority_order.desc()).first()
            priority = (highest.priority_order + 1) if highest else 1
        else:
            priority = req.priority_order

        item = UpgradeQueue(
            name=req.name, target_level=req.target_level,
            resource_type=req.resource_type, upgrade_type=req.upgrade_type,
            priority_order=priority,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return UpgradeItemResponse(
            id=item.id, name=item.name, target_level=item.target_level,
            resource_type=item.resource_type, upgrade_type=item.upgrade_type,
            cost=item.cost, priority_order=item.priority_order,
            status=item.status, started_at=item.started_at, completed_at=item.completed_at,
        )


@router.put("/queue/{item_id}")
async def update_item(item_id: int, req: UpgradeItemUpdate):
    """Update an upgrade queue item."""
    with get_session() as session:
        item = session.query(UpgradeQueue).get(item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        if req.name is not None:
            item.name = req.name
        if req.target_level is not None:
            item.target_level = req.target_level
        if req.resource_type is not None:
            item.resource_type = req.resource_type
        if req.priority_order is not None:
            item.priority_order = req.priority_order
        if req.status is not None:
            item.status = req.status
            if req.status == "IN_PROGRESS":
                item.started_at = datetime.utcnow()
            elif req.status == "COMPLETED":
                item.completed_at = datetime.utcnow()
        if req.cost is not None:
            item.cost = req.cost
        session.commit()
        return {"ok": True}


@router.delete("/queue/{item_id}")
async def delete_item(item_id: int):
    """Remove an item from the upgrade queue."""
    with get_session() as session:
        item = session.query(UpgradeQueue).get(item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        session.delete(item)
        session.commit()
        return {"ok": True}


@router.patch("/queue/{item_id}/status")
async def update_item_status(item_id: int, status: str = "PENDING", cost: int | None = None):
    """Update an item's status and optionally its cost."""
    with get_session() as session:
        item = session.query(UpgradeQueue).get(item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        item.status = status
        if status == "IN_PROGRESS":
            item.started_at = datetime.utcnow()
        elif status == "COMPLETED":
            item.completed_at = datetime.utcnow()
        if cost is not None:
            item.cost = cost
        session.commit()
        return {"ok": True}


@router.get("/status")
async def upgrade_status():
    """Current upgrade queue status + pending count."""
    with get_session() as session:
        pending = session.query(UpgradeQueue).filter_by(status="PENDING").count()
        in_progress = session.query(UpgradeQueue).filter_by(status="IN_PROGRESS").count()
        completed = session.query(UpgradeQueue).filter_by(status="COMPLETED").count()
        return {
            "pending": pending,
            "in_progress": in_progress,
            "completed": completed,
            "total": pending + in_progress + completed,
        }
```

- [ ] **Step 2: Verify syntax and import**

Run: `python -c "from backend.api.rest_upgrade import router; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/api/rest_upgrade.py
git commit -m "feat: add REST API for upgrade queue CRUD
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 3: Register upgrade router in main.py

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: Add router import and registration**

Add after the last router import at line 61:

```python
from backend.api.rest_upgrade import router as upgrade_router
```

Add after the last `app.include_router` at line 70:

```python
app.include_router(upgrade_router)
```

- [ ] **Step 2: Verify**

Run: `python -c "from backend.main import app; routes = [r.path for r in app.routes if hasattr(r, 'path')]; assert '/api/v1/upgrade/queue' in routes; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "feat: register upgrade REST API router
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 4: Add upgrade step types to sequence runner

**Files:**
- Modify: `backend/engine/sequence_runner.py:88-115`

- [ ] **Step 1: Add upgrade step dispatch**

In `_execute_step()`, add after the `return_home` branch (line 115):

```python
        elif stype == "upgrade_check":
            await self._do_upgrade_check(adb)
        elif stype == "upgrade_execute":
            await self._do_upgrade_execute(adb)
```

- [ ] **Step 2: Add stub methods**

Before `_do_return_home()`, add stubs:

```python
    async def _do_upgrade_check(self, adb):
        """Check upgrade queue for affordable upgrades with available builders."""
        from backend.db.database import get_session
        from backend.db.models import UpgradeQueue
        from backend.vision.ocr import read_number

        self.state = "UPGRADE_CHECK"
        logger.info("Checking upgrade queue...")

        screen = await adb.screencap()
        if not screen:
            self._upgrade_item = None
            return

        with get_session() as session:
            items = session.query(UpgradeQueue).filter_by(
                status="PENDING", upgrade_type="building"
            ).order_by(UpgradeQueue.priority_order).all()

        if not items:
            logger.info("No PENDING building upgrades in queue")
            self._upgrade_item = None
            return

        # Read resources
        with get_session() as session:
            from backend.db.models import RoiTemplate
            gold_roi = session.query(RoiTemplate).filter_by(roi_name="gold_number").first()
            elixir_roi = session.query(RoiTemplate).filter_by(roi_name="elixir_number").first()
            de_roi = session.query(RoiTemplate).filter_by(roi_name="dark_elixir_number").first()

        gold = gold_roi and read_number(screen, gold_roi.x_pos, gold_roi.y_pos, gold_roi.width, gold_roi.height, roi_name=gold_roi.roi_name)
        elixir = elixir_roi and read_number(screen, elixir_roi.x_pos, elixir_roi.y_pos, elixir_roi.width, elixir_roi.height, roi_name=elixir_roi.roi_name)
        de = de_roi and read_number(screen, de_roi.x_pos, de_roi.y_pos, de_roi.width, de_roi.height, roi_name=de_roi.roi_name)
        resources = {"gold": gold or 0, "elixir": elixir or 0, "dark_elixir": de or 0}
        logger.info("Resources: G=%d E=%d DE=%d", resources["gold"], resources["elixir"], resources["dark_elixir"])

        # Check builder count
        builder_roi = session.query(RoiTemplate).filter_by(roi_name="builder_count").first() if 'session' in dir() else None
        free_builders = 5  # default
        if builder_roi:
            bc = read_number(screen, builder_roi.x_pos, builder_roi.y_pos, builder_roi.width, builder_roi.height, roi_name="builder_count")
            if bc is not None:
                free_builders = bc
        logger.info("Free builders: %d", free_builders)

        if free_builders < 1:
            logger.info("No free builders — skipping upgrades")
            self._upgrade_item = None
            return

        # Find first affordable item
        for item in items:
            res_val = resources.get(item.resource_type, 0)
            if item.cost and item.cost > 0 and res_val < item.cost:
                logger.info("  %s lvl %d: need %d %s, have %d — skip",
                             item.name, item.target_level, item.cost, item.resource_type, res_val)
                continue
            # Affordable (or cost unknown — will OCR in upgrade_execute)
            self._upgrade_item = item
            logger.info("Selected: %s lvl %d (cost=%s %s)",
                         item.name, item.target_level, item.cost or "?",
                         item.resource_type)
            return

        logger.info("No affordable upgrades found")
        self._upgrade_item = None

    async def _do_upgrade_execute(self, adb):
        """Execute the upgrade selected by _do_upgrade_check."""
        from backend.db.database import get_session
        from backend.db.models import RoiTemplate
        from backend.vision.ocr import read_number
        from datetime import datetime

        if not getattr(self, "_upgrade_item", None):
            logger.info("No upgrade item selected — skipping")
            return

        item = self._upgrade_item
        self.state = "UPGRADING"
        logger.info("Executing upgrade: %s lvl %d", item.name, item.target_level)

        screen = await adb.screencap()
        if not screen:
            return

        # Tap builder menu button
        with get_session() as session:
            menu_roi = session.query(RoiTemplate).filter_by(roi_name="builder_menu").first()

        if menu_roi:
            cx = menu_roi.x_pos + menu_roi.width // 2
            cy = menu_roi.y_pos + menu_roi.height // 2
            await human_tap(adb, cx, cy, sigma=5)
            await human_delay(1.0, 2.0)

        # Read cost from upgrade screen
        with get_session() as session:
            cost_roi = session.query(RoiTemplate).filter_by(roi_name="upgrade_cost").first()

        detected_cost = None
        if cost_roi:
            screen2 = await adb.screencap()
            if screen2:
                detected_cost = read_number(screen2, cost_roi.x_pos, cost_roi.y_pos,
                                            cost_roi.width, cost_roi.height,
                                            roi_name="upgrade_cost")
        logger.info("Detected upgrade cost: %s", detected_cost)

        # Tap upgrade button
        with get_session() as session:
            btn_upgrade = session.query(RoiTemplate).filter_by(roi_name="btn_upgrade").first()
        if btn_upgrade:
            cx = btn_upgrade.x_pos + btn_upgrade.width // 2
            cy = btn_upgrade.y_pos + btn_upgrade.height // 2
            await human_tap(adb, cx, cy, sigma=3)
            await human_delay(0.5, 1.0)

        # Tap confirm button
        with get_session() as session:
            btn_confirm = session.query(RoiTemplate).filter_by(roi_name="btn_upgrade_confirm").first()
        if btn_confirm:
            cx = btn_confirm.x_pos + btn_confirm.width // 2
            cy = btn_confirm.y_pos + btn_confirm.height // 2
            await human_tap(adb, cx, cy, sigma=3)
            await human_delay(0.5, 1.0)

        # Update DB
        with get_session() as session:
            db_item = session.query(UpgradeQueue).get(item.id)
            if db_item:
                db_item.status = "IN_PROGRESS"
                db_item.started_at = datetime.utcnow()
                if detected_cost:
                    db_item.cost = detected_cost
                session.commit()

        logger.info("Upgrade started: %s lvl %d (cost=%d)", item.name, item.target_level, detected_cost or 0)
        self._upgrade_item = None
        await human_delay(1.0, 2.0)
```

The second stub needs `from backend.db.models import UpgradeQueue` — add the import to the second method. Use a local import inside the method.

- [ ] **Step 2: Add _upgrade_item attribute to __init__**

In `SequenceRunner.__init__()`, add after line 29:

```python
        self._upgrade_item = None  # holds selected UpgradeQueue item between check/execute
```

- [ ] **Step 3: Verify syntax and import**

Run: `python -c "from backend.engine.sequence_runner import SequenceRunner; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/engine/sequence_runner.py
git commit -m "feat: add upgrade_check and upgrade_execute step types to SequenceRunner
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 5: Update seed data with upgrade steps in default sequence

**Files:**
- Modify: `backend/db/database.py:72-82`

- [ ] **Step 1: Add upgrade steps to default sequence**

In `init_db()`, after the `return_home` step (around line 80), add:

```python
        session.add(SequenceStep(sequence_id=default_seq.id, step_order=9, step_type="upgrade_check"))
        session.add(SequenceStep(sequence_id=default_seq.id, step_order=10, step_type="upgrade_execute"))
```

Note: update step_order numbers — the existing steps end at step_order=8 (return_home).

Also add new step_types to the `SequenceStep.step_type` field comment in `models.py` line 62: change `# tap, wait, search, attack, return_home` to include `upgrade_check, upgrade_execute`.

- [ ] **Step 2: Verify by reading active sequence**

Run: `python -c "from backend.db.database import init_db; init_db(); from backend.db.database import get_session; from backend.db.models import SequenceStep, BotSequence; s=get_session(); s2=s.__enter__(); steps=s2.query(SequenceStep).join(BotSequence).filter(BotSequence.is_active==True).order_by(SequenceStep.step_order).all(); [print(f'{st.step_order}: {st.step_type}') for st in steps]; s.__exit__(None,None,None)"`
Expected output includes `9: upgrade_check` and `10: upgrade_execute`

- [ ] **Step 3: Commit**

```bash
git add backend/db/database.py backend/db/models.py
git commit -m "feat: add upgrade steps to default attack sequence seed
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 6: Frontend — Builder tab wire-up

**Files:**
- Modify: `frontend/index.html:331-348`
- Modify: `frontend/src/main.js`

- [ ] **Step 1: Add Alpine.js state for builder tab**

In `main.js`, add to the Alpine data object (around line 17):

```javascript
    // Builder tab
    upgradeQueue: [],
    newUpgradeName: "",
    newUpgradeLevel: 1,
    newUpgradeResource: "gold",
    upgradeStatus: { pending: 0, in_progress: 0, completed: 0, total: 0 },
```

And add methods (after existing methods, before the closing `}` of the Alpine object):

```javascript
    // --- Builder / Upgrade Queue ---
    async loadUpgradeQueue() {
      const res = await fetch("/api/v1/upgrade/queue");
      this.upgradeQueue = await res.json();
    },
    async loadUpgradeStatus() {
      const res = await fetch("/api/v1/upgrade/status");
      this.upgradeStatus = await res.json();
    },
    async addUpgradeItem() {
      if (!this.newUpgradeName.trim()) return;
      await fetch("/api/v1/upgrade/queue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: this.newUpgradeName.trim(),
          target_level: parseInt(this.newUpgradeLevel) || 1,
          resource_type: this.newUpgradeResource,
          upgrade_type: "building",
        }),
      });
      this.newUpgradeName = "";
      this.newUpgradeLevel = 1;
      this.newUpgradeResource = "gold";
      await this.loadUpgradeQueue();
      await this.loadUpgradeStatus();
    },
    async deleteUpgradeItem(id) {
      await fetch(`/api/v1/upgrade/queue/${id}`, { method: "DELETE" });
      await this.loadUpgradeQueue();
      await this.loadUpgradeStatus();
    },
    async moveUpgradeItem(id, dir) {
      const idx = this.upgradeQueue.findIndex(i => i.id === id);
      if (idx < 0) return;
      const target = idx + dir;
      if (target < 0 || target >= this.upgradeQueue.length) return;
      const a = this.upgradeQueue[idx], b = this.upgradeQueue[target];
      await fetch(`/api/v1/upgrade/queue/${a.id}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ priority_order: b.priority_order }),
      });
      await fetch(`/api/v1/upgrade/queue/${b.id}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ priority_order: a.priority_order }),
      });
      await this.loadUpgradeQueue();
    },
```

Add to the `activeTab` watcher (around line 97):

```javascript
        if (tab === 'builder') {
          this.loadUpgradeQueue();
          this.loadUpgradeStatus();
        }
```

- [ ] **Step 2: Replace builder tab HTML in index.html**

Replace lines 331-348 with:

```html
    <!-- Tab: Builder -->
    <div x-show="activeTab === 'builder'" class="max-w-lg">
      <div class="bg-slate-800 rounded-lg border border-slate-700 p-4 mb-4">
        <h3 class="text-xs uppercase tracking-wider text-slate-400 mb-3">Upgrade Status</h3>
        <div class="flex gap-4 text-sm">
          <div class="bg-slate-700 rounded px-3 py-2 text-center">
            <span class="block text-lg font-bold text-yellow-400" x-text="upgradeStatus.pending"></span>
            <span class="text-slate-400 text-xs">PENDING</span>
          </div>
          <div class="bg-slate-700 rounded px-3 py-2 text-center">
            <span class="block text-lg font-bold text-blue-400" x-text="upgradeStatus.in_progress"></span>
            <span class="text-slate-400 text-xs">IN PROGRESS</span>
          </div>
          <div class="bg-slate-700 rounded px-3 py-2 text-center">
            <span class="block text-lg font-bold text-green-400" x-text="upgradeStatus.completed"></span>
            <span class="text-slate-400 text-xs">COMPLETED</span>
          </div>
        </div>
      </div>

      <div class="bg-slate-800 rounded-lg border border-slate-700 p-4">
        <h3 class="text-xs uppercase tracking-wider text-slate-400 mb-3">Upgrade Queue</h3>

        <div class="space-y-1 mb-3">
          <template x-for="(item, idx) in upgradeQueue" :key="item.id">
            <div class="flex items-center gap-2 bg-slate-700 rounded p-2 text-xs">
              <span class="text-slate-500 w-5 text-right" x-text="idx+1"></span>
              <span class="font-medium flex-1" x-text="item.name + ' Lvl ' + item.target_level"></span>
              <span class="text-slate-400" x-text="item.cost ? item.cost.toLocaleString() + ' ' + item.resource_type : 'cost unknown'"></span>
              <span class="px-1.5 py-0.5 rounded text-[10px] font-medium"
                :class="item.status === 'PENDING' ? 'bg-yellow-800 text-yellow-300' : item.status === 'IN_PROGRESS' ? 'bg-blue-800 text-blue-300' : 'bg-green-800 text-green-300'"
                x-text="item.status"></span>
              <button @click="moveUpgradeItem(item.id, -1)" :disabled="idx===0"
                class="text-slate-500 hover:text-slate-300 disabled:opacity-30">&uarr;</button>
              <button @click="moveUpgradeItem(item.id, 1)" :disabled="idx===upgradeQueue.length-1"
                class="text-slate-500 hover:text-slate-300 disabled:opacity-30">&darr;</button>
              <button @click="deleteUpgradeItem(item.id)" class="text-red-500 hover:text-red-300">&times;</button>
            </div>
          </template>
          <p x-show="upgradeQueue.length === 0" class="text-slate-500 text-sm py-2">No upgrades queued. Add buildings below.</p>
        </div>

        <div class="flex gap-2 items-end">
          <input x-model="newUpgradeName" placeholder="Building name..." @keyup.enter="addUpgradeItem()"
            class="bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-xs flex-1" />
          <input x-model="newUpgradeLevel" type="number" min="1" placeholder="Lvl"
            class="bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-xs w-16" />
          <select x-model="newUpgradeResource" class="bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-xs">
            <option value="gold">Gold</option>
            <option value="elixir">Elixir</option>
            <option value="dark_elixir">Dark Elixir</option>
          </select>
          <button @click="addUpgradeItem()" :disabled="!newUpgradeName.trim()"
            class="bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white text-xs px-3 py-1.5 rounded transition-colors">Add</button>
        </div>
      </div>
    </div>
```

- [ ] **Step 3: Rebuild frontend**

Run: `cd frontend && npm run build 2>&1 | tail -5`
Expected: Build completes without errors

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html frontend/src/main.js frontend/dist/
git commit -m "feat: wire builder tab with upgrade queue CRUD and status
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 7: Frontend — Add upgrade step types to sequence editor

**Files:**
- Modify: `frontend/index.html:312-318`

- [ ] **Step 1: Add upgrade options to step type dropdown**

Add after line 317 (`<option value="return_home">Return Home</option>`):

```html
            <option value="upgrade_check">Upgrade Check</option>
            <option value="upgrade_execute">Upgrade Execute</option>
```

- [ ] **Step 2: Add border color for upgrade steps in the list**

Update the `:class` on the step item div (line 297) to include upgrade steps:

Find the line with `:class="step.step_type === 'search' ? ..."` and add `upgrade_check` and `upgrade_execute` colors:

```html
              :class="step.step_type === 'search' ? 'border-yellow-700' : step.step_type === 'attack' ? 'border-red-700' : step.step_type === 'upgrade_check' ? 'border-purple-700' : step.step_type === 'upgrade_execute' ? 'border-purple-700' : ''"
```

And update the font color line (300-301):

```html
                :class="step.step_type === 'tap' ? 'text-green-400' : step.step_type === 'search' ? 'text-yellow-400' : step.step_type === 'attack' ? 'text-red-400' : step.step_type === 'upgrade_check' ? 'text-purple-400' : step.step_type === 'upgrade_execute' ? 'text-purple-400' : 'text-blue-400'"
```

- [ ] **Step 3: Rebuild frontend**

Run: `cd frontend && npm run build 2>&1 | tail -5`
Expected: Build completes without errors

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html frontend/dist/
git commit -m "feat: add upgrade_check and upgrade_execute to sequence editor
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 8: Write tests

**Files:**
- Create: `tests/test_upgrade.py`

- [ ] **Step 1: Write upgrade queue API tests**

Create `tests/test_upgrade.py`:

```python
"""Tests for upgrade queue REST API."""

import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.db.database import get_session, init_db
from backend.db.models import UpgradeQueue


@pytest.fixture
def client():
    """Create TestClient and clean upgrade queue before each test."""
    init_db()
    # Clean upgrade queue
    with get_session() as session:
        session.query(UpgradeQueue).delete()
        session.commit()
    return TestClient(app)


def test_create_and_list_queue(client: TestClient):
    """Create items and list them in priority order."""
    r1 = client.post("/api/v1/upgrade/queue", json={
        "name": "Archer Tower", "target_level": 12, "resource_type": "gold"
    })
    assert r1.status_code == 200
    assert r1.json()["priority_order"] == 1

    r2 = client.post("/api/v1/upgrade/queue", json={
        "name": "Wizard Tower", "target_level": 10, "resource_type": "elixir"
    })
    assert r2.status_code == 200
    assert r2.json()["priority_order"] == 2

    items = client.get("/api/v1/upgrade/queue").json()
    assert len(items) == 2
    assert items[0]["name"] == "Archer Tower"
    assert items[1]["name"] == "Wizard Tower"


def test_update_item_status(client: TestClient):
    """Update an item's status."""
    r = client.post("/api/v1/upgrade/queue", json={
        "name": "Test Building", "target_level": 5, "resource_type": "gold"
    })
    item_id = r.json()["id"]

    r2 = client.patch(f"/api/v1/upgrade/queue/{item_id}/status?status=IN_PROGRESS&cost=800000")
    assert r2.status_code == 200

    updated = client.get("/api/v1/upgrade/queue").json()
    item = [i for i in updated if i["id"] == item_id][0]
    assert item["status"] == "IN_PROGRESS"
    assert item["cost"] == 800000
    assert item["started_at"] is not None


def test_delete_item(client: TestClient):
    """Delete an item."""
    r = client.post("/api/v1/upgrade/queue", json={
        "name": "To Delete", "target_level": 3, "resource_type": "elixir"
    })
    item_id = r.json()["id"]
    items_before = client.get("/api/v1/upgrade/queue").json()

    r2 = client.delete(f"/api/v1/upgrade/queue/{item_id}")
    assert r2.status_code == 200

    items_after = client.get("/api/v1/upgrade/queue").json()
    assert len(items_after) == len(items_before) - 1


def test_upgrade_status(client: TestClient):
    """Status endpoint returns counts."""
    r = client.get("/api/v1/upgrade/status")
    assert r.status_code == 200
    data = r.json()
    assert "pending" in data
    assert "in_progress" in data
    assert "completed" in data
    assert "total" in data
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_upgrade.py -v`
Expected: 4 tests pass

- [ ] **Step 3: Commit**

```bash
git add tests/test_upgrade.py
git commit -m "test: add upgrade queue REST API tests
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 9: Final verification

**Files:**
- No file changes, verification only

- [ ] **Step 1: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All tests pass (existing + new upgrade tests)

- [ ] **Step 2: Verify full import chain**

Run: `python -c "from backend.main import app; print('Full import OK')"`
Expected: `Full import OK`

- [ ] **Step 3: Verify upgrade endpoints are registered**

Run: `python -c "from backend.main import app; routes = [r.path for r in app.routes if hasattr(r, 'path')]; assert '/api/v1/upgrade/queue' in routes; assert '/api/v1/upgrade/status' in routes; print('All upgrade routes registered')"`
Expected: `All upgrade routes registered`

- [ ] **Step 4: Commit final state**

```bash
git status
git commit -m "chore: final verification — all tests pass, upgrade system ready
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```
