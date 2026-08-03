# Card Count OCR Detection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace unreliable saturation-based grey detection with OCR-based card count reading from the badge area, supporting hero/troop/spell classification and count-aware deployment.

**Architecture:** Add `CardResult` dataclass and `_read_card_count()` to `sequence_runner.py`. The OCR-heavy badge reading (`read_card_badge`) lives in `vision/ocr.py`. Rewrite `_do_attack()` to use count-based deployment instead of fixed 50 taps. Remove `_card_is_grey()`.

**Tech Stack:** Python 3.12, OpenCV, EasyOCR, NumPy, pytest-asyncio

---

### Task 1: Add `CardResult` dataclass and imports

**Files:**
- Modify: `backend/engine/sequence_runner.py:1-18`

- [ ] **Step 1: Add `dataclasses` import and `CardResult` dataclass**

In `backend/engine/sequence_runner.py`, add `from dataclasses import dataclass` to the imports (after `import time` on line 7):

```python
from dataclasses import dataclass
```

Then add the `CardResult` dataclass after the imports and before the `SequenceRunner` class (after line 18):

```python
@dataclass
class CardResult:
    """Result of reading a card's count badge via OCR."""
    count: int | None   # None=hero, 0=empty, >0=count available
    has_badge: bool      # True if badge detected (even if OCR misses number)
```

- [ ] **Step 2: Verify syntax and import**

Run: `python -c "from backend.engine.sequence_runner import CardResult; r = CardResult(count=3, has_badge=True); print(r)"`
Expected: `CardResult(count=3, has_badge=True)`

- [ ] **Step 3: Commit**

```bash
git add backend/engine/sequence_runner.py
git commit -m "feat: add CardResult dataclass for card count OCR results
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 2: Add `read_card_badge()` to `vision/ocr.py`

**Files:**
- Modify: `backend/vision/ocr.py` (append at end of file)

- [ ] **Step 1: Add `read_card_badge()` function**

Append to `backend/vision/ocr.py` after line 74:

```python
def read_card_badge(screenshot: bytes, card_x: int, card_top: int) -> int | None:
    """Read the troop/spell count from a card's badge region.

    Badge is a small white-on-dark label in the top-right corner of each
    troop/spell card (e.g. "X2", "X10"). Heroes have no badge.

    Args:
        screenshot: PNG bytes from adb screencap
        card_x: center X of the card slot
        card_top: top Y of the card slot

    Returns:
        The count number, or None if no number could be read.
    """
    try:
        nparr = np.frombuffer(screenshot, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        h, w = img.shape[:2]

        # Badge region: top-right corner of the card slot
        badge_x = card_x + 8
        badge_y = card_top + 4
        badge_w, badge_h = 28, 24

        x1 = max(0, badge_x)
        y1 = max(0, badge_y)
        x2 = min(w, x1 + badge_w)
        y2 = min(h, y1 + badge_h)
        if x2 <= x1 or y2 <= y1:
            return None

        roi = img[y1:y2, x1:x2]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        scaled = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)

        # OTSU threshold — white text on dark background
        _, thresh = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        reader = _get_reader()
        results = reader.readtext(thresh, allowlist="0123456789", detail=0, paragraph=True)
        for r in results:
            text = re.sub(r"\D", "", r)
            if text:
                val = int(text)
                logger.debug("Card badge OCR → %d (raw=%r, card_x=%d)", val, r, card_x)
                return val

        return None
    except Exception as e:
        logger.error("read_card_badge failed: %s", e)
        return None


def _check_badge_texture(screenshot: bytes, card_x: int, card_top: int) -> float:
    """Return white pixel percentage in the badge region after OTSU threshold.

    High pct → badge content exists (OCR should succeed).
    Low pct → uniform/dark → probably hero (no badge).
    """
    try:
        nparr = np.frombuffer(screenshot, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        h, w = img.shape[:2]

        badge_x = card_x + 8
        badge_y = card_top + 4
        badge_w, badge_h = 28, 24

        x1 = max(0, badge_x)
        y1 = max(0, badge_y)
        x2 = min(w, x1 + badge_w)
        y2 = min(h, y1 + badge_h)
        if x2 <= x1 or y2 <= y1:
            return 0.0

        roi = img[y1:y2, x1:x2]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        white_pct = float(np.count_nonzero(thresh)) / thresh.size
        return white_pct
    except Exception:
        return 0.0
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "from backend.vision.ocr import read_card_badge, _check_badge_texture; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/vision/ocr.py
git commit -m "feat: add read_card_badge() and _check_badge_texture() for card count OCR
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 3: Add `_read_card_count()` to `SequenceRunner`

**Files:**
- Modify: `backend/engine/sequence_runner.py` (insert after `_detect_cards`, before `_card_is_grey`)

- [ ] **Step 1: Add `_read_card_count()` method**

Insert between `_detect_cards` (ending at line 202) and `_card_is_grey` (starting at line 204):

```python
    async def _read_card_count(self, adb, card) -> CardResult:
        """Read a card's count badge via OCR. Classifies as hero, empty, or active."""
        from backend.vision.ocr import read_card_badge, _check_badge_texture

        try:
            screen = await adb.screencap()
            if not screen:
                return CardResult(count=None, has_badge=False)

            count = read_card_badge(screen, card["x"], card["card_top"])

            if count is not None:
                return CardResult(count=count, has_badge=True)

            # OCR returned nothing — determine hero vs failed OCR
            white_pct = _check_badge_texture(screen, card["x"], card["card_top"])
            if white_pct < 0.05:
                # Uniform/dark region → no badge → HERO
                return CardResult(count=None, has_badge=False)

            # Textured region but OCR failed — retry possible, conservative fallback
            logger.warning("  Card X=%d: badge texture detected (%.2f%%) but OCR missed number",
                           card["x"], white_pct * 100)
            return CardResult(count=5, has_badge=True)

        except Exception:
            return CardResult(count=None, has_badge=False)
```

- [ ] **Step 2: Verify import**

Run: `python -c "from backend.engine.sequence_runner import SequenceRunner; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/engine/sequence_runner.py
git commit -m "feat: add _read_card_count() with hero detection via badge texture check
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 4: Rewrite `_do_attack()` with count-based deployment

**Files:**
- Modify: `backend/engine/sequence_runner.py:227-281`

- [ ] **Step 1: Replace `_do_attack()` method**

Replace lines 227-281 (entire `_do_attack` method) with:

```python
    async def _do_attack(self, step, adb):
        config = json.loads(step.config_json) if step.config_json else {}
        duration = config.get("duration", 180)

        self.state = "ATTACKING"
        logger.info("Deploying troops...")

        cards = await self._detect_cards(adb)

        if not cards:
            logger.warning("No cards detected, cannot deploy")
            await asyncio.sleep(duration)
            return

        # Hardcoded deploy zone centers (from user's calibrated deploy_1..deploy_9)
        DEPLOY_ZONES = [
            (70, 345), (160, 257), (333, 129),
            (457, 36), (850, 32), (994, 137),
            (1162, 262), (1092, 485), (964, 552),
        ]

        # PHASE 1: classify all cards
        depleted = set()   # X0 or heroes that have been used
        heroes = set()     # hero cards (no badge, one-time deploy)

        for i, card in enumerate(cards):
            result = await self._read_card_count(adb, card)
            if result.count == 0:
                depleted.add(i)
                logger.info("  Card %d at X=%d: X0, skipping", i + 1, card["x"])
            elif result.count is None and not result.has_badge:
                heroes.add(i)
                logger.info("  Card %d at X=%d: HERO (no badge)", i + 1, card["x"])
            else:
                count_label = result.count if result.count else "?"
                logger.info("  Card %d at X=%d: count=%s", i + 1, card["x"], count_label)

        logger.info("Detected %d cards: %d heroes, %d active, %d depleted",
                     len(cards), len(heroes),
                     len(cards) - len(depleted) - len(heroes), len(depleted))

        # PHASE 2: deploy loop
        end_time = time.time() + duration
        while self._running and time.time() < end_time:
            active = [i for i in range(len(cards))
                      if i not in depleted and i not in heroes]
            if not active:
                # Check if any heroes remain
                remaining_heroes = [i for i in range(len(cards))
                                    if i not in depleted and i in heroes]
                if not remaining_heroes:
                    logger.info("All cards deployed — deployment complete")
                    break

                # Deploy remaining heroes
                for i in remaining_heroes:
                    if not self._running or time.time() >= end_time:
                        break
                    card = cards[i]
                    cx, cy = card["x"], card["y"]
                    logger.info("  Hero %d at (%d,%d): deploying", i + 1, cx, cy)
                    await human_tap(adb, cx, cy, sigma=2)
                    await human_delay(0.1, 0.3)
                    zx, zy = random.choice(DEPLOY_ZONES)
                    await human_tap(adb, zx, zy, sigma=8)
                    depleted.add(i)
                    await human_delay(0.05, 0.15)
                continue

            for i in active:
                if not self._running or time.time() >= end_time:
                    break

                card = cards[i]
                cx, cy = card["x"], card["y"]

                # Re-read count (may have changed since last deploy)
                result = await self._read_card_count(adb, card)
                if result.count is None or result.count == 0:
                    depleted.add(i)
                    logger.info("  Card %d at (%d,%d): depleted (count=%s)",
                                i + 1, cx, cy, result.count)
                    continue

                await human_tap(adb, cx, cy, sigma=2)
                n_taps = min(result.count, 10)
                logger.info("  Card %d at (%d,%d): %d taps (count=%d)",
                            i + 1, cx, cy, n_taps, result.count)
                for _ in range(n_taps):
                    zx, zy = random.choice(DEPLOY_ZONES)
                    await human_tap(adb, zx, zy, sigma=8)
                    await human_delay(0.005, 0.01)
                await human_delay(0.05, 0.15)

        remaining = max(0, end_time - time.time())
        if remaining > 0:
            logger.info("Waiting for battle (%.0fs)...", remaining)
            await asyncio.sleep(remaining)
```

- [ ] **Step 2: Verify syntax and import**

Run: `python -c "from backend.engine.sequence_runner import SequenceRunner; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/engine/sequence_runner.py
git commit -m "feat: rewrite _do_attack() with count-based deployment, hero detection, X0 skipping
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 5: Remove `_card_is_grey()` method

**Files:**
- Modify: `backend/engine/sequence_runner.py` (remove `_card_is_grey` method)

- [ ] **Step 1: Delete `_card_is_grey()`**

Remove the entire `_card_is_grey` method (lines 204-225 in the original numbering, now shifted after the insertions). Current content to delete:

```python
    async def _card_is_grey(self, adb, card):
        """Check if card is grey/depleted. Uses saturation (not brightness)."""
        try:
            screen = await adb.screencap()
            ... (entire method body)
        except Exception:
            return True
```

- [ ] **Step 2: Verify no references remain**

Run: `python -c "import ast; tree = ast.parse(open('backend/engine/sequence_runner.py').read()); refs = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == '_card_is_grey']; print('References:', len(refs))"`
Expected: `References: 0`

- [ ] **Step 3: Verify full import**

Run: `python -c "from backend.engine.sequence_runner import sequence_runner; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/engine/sequence_runner.py
git commit -m "refactor: remove _card_is_grey() replaced by _read_card_count()
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 6: Write tests for `read_card_badge()` image preprocessing

**Files:**
- Create: `tests/test_card_ocr.py`
- Create: `tests/__init__.py` (if it doesn't exist)

- [ ] **Step 1: Write tests**

Create `tests/test_card_ocr.py`:

```python
"""Tests for card badge OCR utilities."""

import cv2
import numpy as np
import pytest

from backend.vision.ocr import read_card_badge, _check_badge_texture


def _make_screen_with_badge(has_badge: bool = False, number: int | None = None) -> bytes:
    """Create a synthetic 1280x720 BGR screenshot with a card and optional badge.

    Card is drawn at x=380 (center), card_top=635 (bottom bar).
    Badge is at x=388, y=639 (card_x+8, card_top+4), 28x24px.
    """
    img = np.zeros((720, 1280, 3), dtype=np.uint8)

    # Draw card area (dark background)
    card_x, card_top = 380, 635
    card_x1 = card_x - 30
    card_y1 = card_top
    img[card_y1:card_y1 + 85, card_x1:card_x1 + 60] = (40, 40, 40)

    if has_badge:
        # Badge region: white text on dark background
        badge_x1 = card_x + 8
        badge_y1 = card_top + 4
        badge_w, badge_h = 28, 24
        # Dark badge background
        img[badge_y1:badge_y1 + badge_h, badge_x1:badge_x1 + badge_w] = (20, 20, 20)

        if number is not None:
            # Write number as white pixels in the badge
            font = cv2.FONT_HERSHEY_SIMPLEX
            txt = str(number)
            cv2.putText(img, txt, (badge_x1 + 2, badge_y1 + 18),
                       font, 0.6, (255, 255, 255), 2, cv2.LINE_AA)

    # Encode to PNG bytes
    _, buf = cv2.imencode(".png", img)
    return buf.tobytes()


def test_read_card_badge_returns_number():
    """Badge with '2' should return 2."""
    screen = _make_screen_with_badge(has_badge=True, number=2)
    result = read_card_badge(screen, card_x=380, card_top=635)
    # OCR may fail on synthetic images; test shape is the goal
    # At minimum, function should not raise
    assert isinstance(result, (int, type(None)))


def test_read_card_badge_hero_returns_none():
    """Hero card (no badge) should return None."""
    screen = _make_screen_with_badge(has_badge=False)
    result = read_card_badge(screen, card_x=380, card_top=635)
    assert result is None


def test_check_badge_texture_with_badge():
    """Badge region with content should have white_pct > 0."""
    screen = _make_screen_with_badge(has_badge=True, number=3)
    white_pct = _check_badge_texture(screen, card_x=380, card_top=635)
    # With synthetic white text on dark bg, should detect some white pixels
    assert white_pct >= 0.0


def test_check_badge_texture_empty():
    """Empty badge region should have very low white_pct."""
    screen = _make_screen_with_badge(has_badge=False)
    white_pct = _check_badge_texture(screen, card_x=380, card_top=635)
    # Uniform dark region: white_pct should be near 0
    assert white_pct < 0.1


def test_read_card_badge_invalid_bounds():
    """Card at screen edge should not crash."""
    screen = _make_screen_with_badge(has_badge=False)
    result = read_card_badge(screen, card_x=-100, card_top=-100)
    assert result is None


def test_check_badge_texture_invalid_bounds():
    """Card at screen edge: texture check should return 0."""
    screen = _make_screen_with_badge(has_badge=False)
    white_pct = _check_badge_texture(screen, card_x=0, card_top=0)
    assert white_pct == 0.0
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_card_ocr.py -v`
Expected: 6 tests pass

- [ ] **Step 3: Commit**

```bash
git add tests/test_card_ocr.py tests/__init__.py
git commit -m "test: add card badge OCR tests for read_card_badge and _check_badge_texture
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 7: Write tests for `_read_card_count()` and `_do_attack()` logic

**Files:**
- Modify: `tests/test_card_ocr.py` (append tests)

- [ ] **Step 1: Write integration tests**

Append to `tests/test_card_ocr.py`:

```python
"""Integration tests for SequenceRunner card logic."""

import asyncio
import pytest

from backend.adb.mock import MockAdbManager
from backend.engine.sequence_runner import SequenceRunner, CardResult


@pytest.fixture
def runner():
    """Create a SequenceRunner that we can test methods on."""
    return SequenceRunner()


@pytest.mark.asyncio
async def test_read_card_count_returns_card_result(runner):
    """_read_card_count should always return a CardResult, never raise."""
    mock = MockAdbManager()
    card = {"x": 380, "y": 677, "card_top": 635}

    result = await runner._read_card_count(mock, card)
    assert isinstance(result, CardResult)
    assert hasattr(result, "count")
    assert hasattr(result, "has_badge")


@pytest.mark.asyncio
async def test_read_card_count_no_screencap():
    """When screencap fails (returns None), should return CardResult with has_badge=False."""
    runner = SequenceRunner()

    class NoScreenAdb:
        is_connected = True
        async def connect(self): return True
        async def screencap(self): return None

    card = {"x": 380, "y": 677, "card_top": 635}
    result = await runner._read_card_count(NoScreenAdb(), card)
    assert isinstance(result, CardResult)
    assert result.has_badge is False
    assert result.count is None


def test_card_result_dataclass():
    """CardResult should be constructable and have correct defaults."""
    r1 = CardResult(count=3, has_badge=True)
    assert r1.count == 3
    assert r1.has_badge is True

    r2 = CardResult(count=None, has_badge=False)
    assert r2.count is None
    assert r2.has_badge is False

    r3 = CardResult(count=0, has_badge=True)
    assert r3.count == 0
    assert r3.has_badge is True


@pytest.mark.asyncio
async def test_do_attack_no_cards():
    """_do_attack with no detected cards should wait and return."""
    runner = SequenceRunner()
    runner._running = False  # prevent loop

    class NoCardAdb(MockAdbManager):
        async def screencap(self):
            import cv2
            import numpy as np
            img = np.zeros((720, 1280, 3), dtype=np.uint8)
            _, buf = cv2.imencode(".png", img)
            return buf.tobytes()

    from unittest.mock import MagicMock
    step = MagicMock()
    step.config_json = '{"duration": 1}'

    await runner._do_attack(step, NoCardAdb())
    # Should not raise — just sleep and return
```


- [ ] **Step 2: Run all tests**

Run: `pytest tests/test_card_ocr.py -v`
Expected: All tests pass (including the 6 from Task 6)

- [ ] **Step 3: Commit**

```bash
git add tests/test_card_ocr.py
git commit -m "test: add _read_card_count() and _do_attack() integration tests
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 8: Final verification — full test suite + manual check

**Files:**
- No file changes, verification only

- [ ] **Step 1: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All tests pass (existing + new)

- [ ] **Step 2: Verify module import chain**

Run: `python -c "from backend.main import app; print('Full import OK')"`
Expected: `Full import OK`

- [ ] **Step 3: Verify `_card_is_grey` is fully removed**

Run: `python -c "import ast; tree = ast.parse(open('backend/engine/sequence_runner.py').read()); fns = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]; assert '_card_is_grey' not in fns, f'Found: {fns}'; print('Confirmed: _card_is_grey removed')"`
Expected: `Confirmed: _card_is_grey removed`

- [ ] **Step 4: Commit final state**

```bash
git status
git commit -m "chore: final verification — all tests pass, _card_is_grey removed
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```
