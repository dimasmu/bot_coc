# Resource Maxed Indicator + Merged Dashboard Card — Design Spec

**Date**: 2026-08-02
**Status**: Approved
**Goal**: (1) Detect when gold/elixir/dark_elixir are maxed for current TH level by tracking stable values across attacks. (2) Merge Bot Status + Resources into a single dashboard card with maxed badges.

## Problem

No visual indicator shows whether resources have hit the TH cap. The player must manually check if gold/elixir numbers have stopped increasing despite successful raids.

## Design

### Feature 1: Maxed Detection Logic

**Detection (after each `read_current_resources()`):**

For each resource independently:
```
if current_value == previous_value:
    stable_count += 1
    if stable_count >= 2 → maxed = true
else:
    stable_count = 0
    maxed = false
previous_value = current_value
```

**Reset (after upgrade):**

In `_do_upgrade_execute()`, when an upgrade is confirmed:
```
if upgrade resource is "gold"     → gold_stable = 0, gold_max = false
if upgrade resource is "elixir"   → elixir_stable = 0, elixir_max = false
if upgrade resource is "dark_elixir" → de_stable = 0, de_max = false
```

### Feature 2: Merged Dashboard Card

Merge current two right-side cards (Bot Status + Resources) into one card with divider. Layout:

```
┌─ Bot Status ─────────────────────┐
│ RUNNING    FARMING               │
│ [Choose Sequence ▾]             │
│ [Start Bot]  [Stop Bot]         │
│ ──────────────────────────────── │
│ Resources                        │
│ Gold     8.669.773  [MAX]       │
│ Elixir   12.719.959 [MAX]       │
│ Dark E.  425.972                │
│ Gems     8.492                   │
└──────────────────────────────────┘
```

Maxed badge: inline yellow tag `[MAX]` next to the value. Only shown when `maxed == true`.

### Backend Changes

**`backend/engine/sequence_runner.py`:**

New instance variables in `__init__`:
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

Modified `read_current_resources()` — after storing values, detect maxed:
```python
# Gold max check
if self.current_gold > 0 and self.current_gold == self._prev_gold:
    self._gold_stable += 1
    if self._gold_stable >= 2:
        self._gold_max = True
else:
    self._gold_stable = 0
    self._gold_max = False
self._prev_gold = self.current_gold

# Same pattern for elixir and dark_elixir...
```

Modified `_do_upgrade_execute()` — after confirm tap, reset max flag:
```python
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

Modified `get_status_dict()`:
```python
"gold_max": self._gold_max,
"elixir_max": self._elixir_max,
"dark_elixir_max": self._dark_elixir_max,
```

### Frontend Changes

**`frontend/src/main.js`:**

Add state variables:
```js
goldMax: false,
elixirMax: false,
darkElixirMax: false,
```

Consume in `connectBotStatus()`:
```js
this.goldMax = data.gold_max ?? false;
this.elixirMax = data.elixir_max ?? false;
this.darkElixirMax = data.dark_elixir_max ?? false;
```

**`frontend/index.html`:**

Merge two cards into one. Replace the entire Status Panel section (Bot Status card + Resources card) with a single card:

```html
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
      class="bg-green-600 hover:bg-green-500 disabled:opacity-50 disabled:cursor-not-allowed text-white px-3 py-1 rounded text-xs transition-colors flex-1">Start Bot</button>
    <button @click="stopBot()" :disabled="!botRunning"
      class="bg-red-600 hover:bg-red-500 disabled:opacity-50 disabled:cursor-not-allowed text-white px-3 py-1 rounded text-xs transition-colors flex-1">Stop Bot</button>
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
```

### Files Changed

| File | Change |
|---|---|
| `backend/engine/sequence_runner.py` | `__init__`: maxed vars. `read_current_resources()`: stable count logic. `_do_upgrade_execute()`: reset. `get_status_dict()`: expose. |
| `frontend/src/main.js` | `goldMax`/`elixirMax`/`darkElixirMax` state + WS consume |
| `frontend/index.html` | Merge 2 cards into 1 with maxed badges |
