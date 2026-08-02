# Resource Maxed Indicator + Merged Dashboard Card — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect maxed gold/elixir/dark_elixir by tracking stable values across attacks (2 consecutive unchanged = maxed). Reset on upgrade. Merge Bot Status + Resources into one dashboard card with inline MAX badges.

**Architecture:** Add stable-count tracking vars to SequenceRunner. Update in `read_current_resources()` after each OCR. Reset in `_do_upgrade_execute()` after confirm. Expose via `get_status_dict()`. Frontend consumes and renders merged card.

**Tech Stack:** Python (asyncio), JavaScript (Alpine.js), Tailwind CSS

---

### Task 1: Add maxed instance vars and expose in get_status_dict

**Files:**
- Modify: `backend/engine/sequence_runner.py` — `__init__` (lines 35-37) and `get_status_dict()` (lines 43-56)

- [ ] **Step 1: Add instance variables in __init__**

After `self._loop_mode = ""` at line 36, add:

```python
        self._gold_max = False
        self._elixir_max = False
        self._dark_elixir_max = False
        self._prev_gold = 0
        self._prev_elixir = 0
        self._prev_dark_elixir = 0
        self._gold_stable = 0
        self._elixir_stable = 0
        self._de_stable = 0
```

- [ ] **Step 2: Add to get_status_dict()**

Add after `"loop_mode": self._loop_mode,` at line 55:

```python
            "gold_max": self._gold_max,
            "elixir_max": self._elixir_max,
            "dark_elixir_max": self._dark_elixir_max,
```

- [ ] **Step 3: Verify syntax and commit**

```bash
python -c "import ast; ast.parse(open('backend/engine/sequence_runner.py').read()); print('OK')"
```

```bash
git add backend/engine/sequence_runner.py
git commit -m "feat: add maxed resource tracking vars and expose in status"
```

---

### Task 2: Add stable-count detection in read_current_resources

**Files:**
- Modify: `backend/engine/sequence_runner.py` — `read_current_resources()` (lines 473-499)

- [ ] **Step 1: Add maxed detection after OCR values are stored**

After the logger.info line at 498-499, add the stable-count logic:

```python
        # Gold maxed detection
        if self.current_gold > 0 and self.current_gold == self._prev_gold:
            self._gold_stable += 1
            if self._gold_stable >= 2:
                self._gold_max = True
        else:
            self._gold_stable = 0
            self._gold_max = False
        self._prev_gold = self.current_gold

        # Elixir maxed detection
        if self.current_elixir > 0 and self.current_elixir == self._prev_elixir:
            self._elixir_stable += 1
            if self._elixir_stable >= 2:
                self._elixir_max = True
        else:
            self._elixir_stable = 0
            self._elixir_max = False
        self._prev_elixir = self.current_elixir

        # Dark elixir maxed detection
        if self.current_dark_elixir > 0 and self.current_dark_elixir == self._prev_dark_elixir:
            self._de_stable += 1
            if self._de_stable >= 2:
                self._dark_elixir_max = True
        else:
            self._de_stable = 0
            self._dark_elixir_max = False
        self._prev_dark_elixir = self.current_dark_elixir

        if self._gold_max or self._elixir_max or self._dark_elixir_max:
            logger.info("Maxed resources: Gold=%s Elixir=%s DE=%s",
                self._gold_max, self._elixir_max, self._dark_elixir_max)
```

- [ ] **Step 2: Verify and commit**

```bash
python -c "import ast; ast.parse(open('backend/engine/sequence_runner.py').read()); print('OK')"
git add backend/engine/sequence_runner.py
git commit -m "feat: detect maxed resources via stable count tracking"
```

---

### Task 3: Reset maxed flags after upgrade

**Files:**
- Modify: `backend/engine/sequence_runner.py` — `_do_upgrade_execute()` (around line 758)

- [ ] **Step 1: Add reset logic after upgrade confirm**

After line 760 (`self._upgrade_target = None`), add:

```python
        # Reset maxed flag for the resource just spent
        res = bld.get("resource", "gold")
        if res == "gold":
            self._gold_stable = 0
            self._gold_max = False
        elif res == "elixir":
            self._elixir_stable = 0
            self._elixir_max = False
        elif res == "dark_elixir":
            self._de_stable = 0
            self._dark_elixir_max = False
```

- [ ] **Step 2: Verify and commit**

```bash
python -c "import ast; ast.parse(open('backend/engine/sequence_runner.py').read()); print('OK')"
git add backend/engine/sequence_runner.py
git commit -m "feat: reset maxed resource flag after upgrade spend"
```

---

### Task 4: Frontend — add maxed state variables and consume from WS

**Files:**
- Modify: `frontend/src/main.js`

- [ ] **Step 1: Add state variables**

After `darkElixirMax: false,` doesn't exist yet — add after `currentGems: 0,`:

```js
    goldMax: false,
    elixirMax: false,
    darkElixirMax: false,
```

- [ ] **Step 2: Consume in connectBotStatus()**

After the `this.loopMode = ...` line, add:

```js
        this.goldMax = data.gold_max ?? false;
        this.elixirMax = data.elixir_max ?? false;
        this.darkElixirMax = data.dark_elixir_max ?? false;
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/main.js
git commit -m "feat: consume maxed resource flags from WS status"
```

---

### Task 5: Frontend — merge cards and add MAX badges

**Files:**
- Modify: `frontend/index.html` — Status Panel section (lines 82-120)

- [ ] **Step 1: Replace the two separate cards with one merged card**

Find the Status Panel div at line 83 (`<div class="w-64 flex flex-col gap-3">`). Replace everything from that div's opening through the closing of the Resources card with:

```html
        <!-- Status Panel -->
        <div class="w-64 flex flex-col gap-3">
          <div class="bg-slate-800 rounded-lg border border-slate-700 p-4">
            <h3 class="text-xs uppercase tracking-wider text-slate-400 mb-2">Bot Status</h3>
            <div class="flex items-center gap-2 mb-2">
              <span class="inline-block px-2 py-1 rounded text-xs font-bold"
                :class="botState === 'STOPPED' ? 'bg-slate-700 text-slate-300' : botState === 'DEAD' ? 'bg-red-900 text-red-300' : 'bg-green-900 text-green-300'"
                x-text="botState">IDLE</span>
              <span x-show="loopMode" class="px-2 py-1 rounded text-xs font-bold"
                :class="loopMode === 'farming' ? 'bg-amber-900 text-amber-300' : 'bg-purple-900 text-purple-300'"
                x-text="loopMode === 'farming' ? 'FARMING' : 'UPGRADE'"></span>
            </div>
            <select x-model="activeSequenceId" class="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs">
              <option :value="null">-- Choose Sequence --</option>
              <template x-for="seq in sequences" :key="seq.id">
                <option :value="seq.id" x-text="seq.name"></option>
              </template>
            </select>
            <div class="flex gap-2 mt-3">
              <button @click="startBot()" :disabled="botRunning || !adbStatus.connected"
                class="bg-green-600 hover:bg-green-500 disabled:opacity-50 disabled:cursor-not-allowed text-white px-3 py-1 rounded text-xs transition-colors flex-1">
                Start Bot
              </button>
              <button @click="stopBot()" :disabled="!botRunning"
                class="bg-red-600 hover:bg-red-500 disabled:opacity-50 disabled:cursor-not-allowed text-white px-3 py-1 rounded text-xs transition-colors flex-1">
                Stop Bot
              </button>
            </div>
            <hr class="border-slate-700 my-3">
            <h3 class="text-xs uppercase tracking-wider text-slate-400 mb-2">Resources</h3>
            <div class="text-sm space-y-1">
              <div class="flex justify-between"><span>Gold</span>
                <span><span class="text-yellow-400" x-text="currentGold.toLocaleString('de-DE')"></span>
                <span x-show="goldMax" class="ml-1 px-1 py-0.5 rounded text-[10px] font-bold bg-yellow-900 text-yellow-300">MAX</span></span></div>
              <div class="flex justify-between"><span>Elixir</span>
                <span><span class="text-pink-400" x-text="currentElixir.toLocaleString('de-DE')"></span>
                <span x-show="elixirMax" class="ml-1 px-1 py-0.5 rounded text-[10px] font-bold bg-pink-900 text-pink-300">MAX</span></span></div>
              <div class="flex justify-between"><span>Dark E.</span>
                <span><span class="text-purple-400" x-text="currentDarkElixir.toLocaleString('de-DE')"></span>
                <span x-show="darkElixirMax" class="ml-1 px-1 py-0.5 rounded text-[10px] font-bold bg-purple-900 text-purple-300">MAX</span></span></div>
              <div class="flex justify-between"><span>Gems</span><span class="text-green-400" x-text="currentGems.toLocaleString('de-DE')"></span></div>
            </div>
          </div>
        </div>
```

Note: the outer `<div class="w-64 flex flex-col gap-3">` remains, now wrapping just one card.

- [ ] **Step 2: Commit**

```bash
git add frontend/index.html
git commit -m "feat: merge dashboard cards and add MAX resource badges"
```

---

### Task 6: Rebuild frontend and verify

- [ ] **Step 1: Rebuild**

```bash
cd frontend && npm run build
```

- [ ] **Step 2: Verify key bindings in dist output**

```bash
grep -c "goldMax" dist/index.html
# Expected: 2 (x-show + no other)
grep -c "elixirMax" dist/index.html
# Expected: 2
grep -c "filteredLogLines" dist/index.html
# Expected: 1 (not regressed)
grep -c "currentGold" dist/index.html
# Expected: 1 (not regressed)
```
