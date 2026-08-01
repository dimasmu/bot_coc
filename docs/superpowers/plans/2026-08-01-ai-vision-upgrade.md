# AI Vision Upgrade — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace template matching in `_do_upgrade_execute` with DashScope qwen3.7-flash vision AI to detect upgradeable buildings from builder menu screenshots.

**Architecture:** New `backend/vision/ai.py` wraps DashScope `MultiModalConversation.call()` to send 1280x720 PNG screenshots and parse JSON responses. `_do_upgrade_execute` in sequence_runner.py gets rewritten: try AI first, fall back to extracted `_do_upgrade_execute_template()` if AI unavailable/fails.

**Tech Stack:** Python 3.12, dashscope, FastAPI, OpenCV, SQLModel

---

### Task 1: Add dashscope dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add dashscope to dependencies**

Add `"dashscope"` to the dependencies list in `pyproject.toml`:

```toml
dependencies = [
    "fastapi[standard]>=0.115",
    "adb-shell>=0.4",
    "opencv-python-headless>=4.10",
    "pytesseract>=0.3",
    "sqlmodel>=0.0",
    "aiosqlite>=0.20",
    "pydantic-settings>=2.0",
    "pillow>=11.0",
    "dashscope",
]
```

- [ ] **Step 2: Install the new dependency**

Run: `uv sync`
Expected: dashscope package installed without errors

- [ ] **Step 3: Verify import works**

Run: `python -c "import dashscope; print('dashscope', dashscope.__version__)"`
Expected: prints version string, no errors

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add dashscope dependency for AI vision
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 2: Create backend/vision/ai.py (TDD)

**Files:**
- Create: `tests/test_ai_upgrade.py`
- Create: `backend/vision/ai.py`

- [ ] **Step 1: Write the test file**

Create `tests/test_ai_upgrade.py`:

```python
"""Tests for DashScope AI vision client — response parsing and prompt building."""

import json
import pytest
from backend.vision.ai import _parse_response, _build_prompt


class TestParseResponse:
    def test_valid_single_building(self):
        text = json.dumps({
            "buildings": [
                {"name": "Archer Tower", "x": 450, "y": 320, "cost": 800000, "resource": "gold"}
            ]
        })
        result = _parse_response(text)
        assert len(result) == 1
        assert result[0]["name"] == "Archer Tower"
        assert result[0]["x"] == 450
        assert result[0]["y"] == 320
        assert result[0]["cost"] == 800000
        assert result[0]["resource"] == "gold"

    def test_valid_multiple_buildings(self):
        text = json.dumps({
            "buildings": [
                {"name": "Cannon", "x": 400, "y": 200, "cost": 500000, "resource": "gold"},
                {"name": "Wizard Tower", "x": 600, "y": 300, "cost": 1200000, "resource": "elixir"},
            ]
        })
        result = _parse_response(text)
        assert len(result) == 2
        assert result[0]["name"] == "Cannon"
        assert result[1]["name"] == "Wizard Tower"

    def test_invalid_json_returns_none(self):
        assert _parse_response("not json at all") is None
        assert _parse_response("") is None
        assert _parse_response(None) is None

    def test_empty_buildings_list(self):
        text = '{"buildings": []}'
        result = _parse_response(text)
        assert result == []

    def test_out_of_bounds_coords_filtered(self):
        text = json.dumps({
            "buildings": [
                {"name": "Valid", "x": 500, "y": 300, "cost": 0},
                {"name": "TooFarX", "x": 9999, "y": 300, "cost": 0},
                {"name": "TooFarY", "x": 500, "y": 9999, "cost": 0},
                {"name": "Negative", "x": -10, "y": 300, "cost": 0},
            ]
        })
        result = _parse_response(text)
        assert len(result) == 1
        assert result[0]["name"] == "Valid"

    def test_markdown_wrapped_json(self):
        text = '```json\n{"buildings": [{"name": "Wall", "x": 100, "y": 100, "cost": 50000, "resource": "gold"}]}\n```'
        result = _parse_response(text)
        assert len(result) == 1
        assert result[0]["name"] == "Wall"

    def test_missing_buildings_key(self):
        assert _parse_response('{"other": "data"}') is None

    def test_buildings_not_a_list(self):
        assert _parse_response('{"buildings": "not a list"}') is None

    def test_building_missing_name(self):
        text = json.dumps({
            "buildings": [{"x": 500, "y": 300, "cost": 0}]
        })
        result = _parse_response(text)
        assert result == []  # filtered out

    def test_building_missing_coords(self):
        text = json.dumps({
            "buildings": [{"name": "No Coords", "cost": 0}]
        })
        result = _parse_response(text)
        assert result == []

    def test_coordinates_converted_to_int(self):
        text = json.dumps({
            "buildings": [{"name": "FloatTown", "x": 450.7, "y": 320.1, "cost": 0}]
        })
        result = _parse_response(text)
        assert result[0]["x"] == 450
        assert result[0]["y"] == 320

    def test_default_resource_gold(self):
        text = json.dumps({
            "buildings": [{"name": "Test", "x": 100, "y": 100, "cost": 0, "resource": "unknown"}]
        })
        result = _parse_response(text)
        assert result[0]["resource"] == "gold"

    def test_capped_at_five(self):
        buildings = [{"name": f"B{i}", "x": 100, "y": 100 + i * 30, "cost": 0} for i in range(10)]
        text = json.dumps({"buildings": buildings})
        result = _parse_response(text)
        assert len(result) == 5


class TestBuildPrompt:
    def test_prompt_contains_required_keywords(self):
        prompt = _build_prompt()
        assert "1280x720" in prompt
        assert "Upgrade" in prompt
        assert "pixel coordinates" in prompt.lower()
        assert "buildings" in prompt
        assert "JSON" in prompt
        assert "0-1279" in prompt
        assert "0-719" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ai_upgrade.py -v`
Expected: All tests FAIL — `ModuleNotFoundError: No module named 'backend.vision.ai'`

- [ ] **Step 3: Create backend/vision/ai.py**

Create `backend/vision/ai.py`:

```python
"""DashScope AI vision client for analyzing Clash of Clans screenshots."""

import json
import logging
import re

logger = logging.getLogger(__name__)

# --- Constants ---
BASE_URL = "https://dashscope-intl.aliyuncs.com/api/v1"
MODEL = "qwen3.7-flash"
SCREEN_WIDTH = 1280
SCREEN_HEIGHT = 720
VALID_RESOURCES = {"gold", "elixir", "dark_elixir"}
MAX_BUILDINGS = 5


def _build_prompt() -> str:
    """Build the strict JSON-output prompt for building detection."""
    return """You are a Clash of Clans bot assistant. Analyze this screenshot (1280x720).
This is the "Builder Suggestions" menu after pressing the builder button.

Find all buildings that have an "Upgrade" button (green text with a cost).
For each upgradable building, provide the EXACT pixel coordinates of
its "Upgrade" button.

Return ONLY valid JSON (no markdown, no explanation):

{
  "buildings": [
    {"name": "Archer Tower", "x": 450, "y": 320, "cost": 800000, "resource": "gold"}
  ]
}

If nothing is upgradable: {"buildings": []}

IMPORTANT RULES:
- x and y MUST be integers in range 0-1279 and 0-719
- cost MUST be an integer (no commas)
- resource: "gold", "elixir", or "dark_elixir" """


def _parse_response(raw_text: str) -> list[dict] | None:
    """Parse AI response text into validated list of building dicts.

    Returns:
        None if parsing/validation fails (indicates fallback needed).
        Empty list if AI says no upgrades available.
        List of dicts with keys: name, x, y, cost, resource.
    """
    if not raw_text or not raw_text.strip():
        return None

    # Strip markdown fences
    text = raw_text.strip()
    text = re.sub(r'^```(?:json)?\s*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("AI response is not valid JSON. Raw: %s", raw_text[:200])
        return None

    if not isinstance(data, dict) or "buildings" not in data:
        logger.warning("AI response missing 'buildings' key. Got: %s", str(data)[:200])
        return None

    buildings_raw = data["buildings"]
    if not isinstance(buildings_raw, list):
        return None

    valid = []
    for b in buildings_raw:
        if not isinstance(b, dict):
            continue
        name = b.get("name", "")
        x = b.get("x")
        y = b.get("y")
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            continue
        x, y = int(x), int(y)
        if not (0 <= x < SCREEN_WIDTH and 0 <= y < SCREEN_HEIGHT):
            logger.warning("Building '%s' coords out of bounds: (%d, %d)", name, x, y)
            continue

        cost = b.get("cost", 0)
        if isinstance(cost, (int, float)):
            cost = int(cost)
        else:
            cost = 0

        resource = b.get("resource", "")
        if resource not in VALID_RESOURCES:
            resource = "gold"

        valid.append({
            "name": name.strip(),
            "x": x,
            "y": y,
            "cost": cost,
            "resource": resource,
        })

    if len(valid) < len(buildings_raw):
        logger.info("Filtered %d invalid building(s)", len(buildings_raw) - len(valid))

    return valid[:MAX_BUILDINGS]


class DashScopeClient:
    """Client for DashScope multimodal vision API."""

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key
        self._prompt = _build_prompt()

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def analyze_screenshot(self, png_bytes: bytes) -> list[dict] | None:
        """Analyze a builder menu screenshot and return upgradable buildings.

        Args:
            png_bytes: Raw PNG image bytes (1280x720).

        Returns:
            List of building dicts, empty list for no upgrades, None on failure.
        """
        if not self.available:
            logger.warning("DashScope API key not configured")
            return None

        try:
            import dashscope
        except ImportError:
            logger.error("dashscope package not installed")
            return None

        dashscope.base_http_api_url = BASE_URL

        import base64
        image_b64 = base64.b64encode(png_bytes).decode("ascii")

        messages = [{
            "role": "user",
            "content": [
                {"image": f"data:image/png;base64,{image_b64}"},
                {"text": self._prompt},
            ]
        }]

        try:
            response = dashscope.MultiModalConversation.call(
                api_key=self._api_key,
                model=MODEL,
                messages=messages,
            )
        except Exception as e:
            logger.error("DashScope API call failed: %s", e)
            return None

        if response is None:
            logger.error("DashScope returned None response")
            return None

        if not hasattr(response, 'output') or response.output is None:
            logger.error("DashScope response has no output")
            return None

        choices = getattr(response.output, 'choices', None)
        if not choices:
            logger.error("DashScope response has no choices")
            return None

        try:
            text = choices[0].message.content[0]["text"]
        except (IndexError, KeyError, TypeError, AttributeError) as e:
            logger.error("Failed to extract text from DashScope response: %s", e)
            return None

        logger.debug("AI raw response: %s", text[:500])
        return _parse_response(text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ai_upgrade.py -v`
Expected: All 13 tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_ai_upgrade.py backend/vision/ai.py
git commit -m "feat: add DashScope AI vision client for upgrade detection
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 3: Rewire _do_upgrade_execute to use AI with template fallback

**Files:**
- Modify: `backend/engine/sequence_runner.py`

- [ ] **Step 1: Add _ai_client to __init__**

In `SequenceRunner.__init__()`, add after `self._upgrade_item = None` (line 30):

```python
        self._ai_client = None  # lazy-init DashScope client
```

- [ ] **Step 2: Add helper method _get_ai_client and _save_debug_screenshot**

Add these methods before `_do_upgrade_check()`. Insert after the `_TPL_CONFIRM` class constant block (after line 449), before line 451 (`async def _do_upgrade_execute`):

```python
    def _get_ai_client(self):
        """Lazy-init the DashScope client from DB config."""
        if self._ai_client is not None:
            return self._ai_client
        from backend.vision.ai import DashScopeClient
        with get_session() as session:
            cfg = session.query(Config).filter_by(key="dashscope_api_key").first()
        api_key = cfg.value.strip() if cfg and cfg.value else None
        self._ai_client = DashScopeClient(api_key=api_key)
        if self._ai_client.available:
            logger.info("DashScope AI client initialized")
        else:
            logger.info("DashScope API key not configured — AI disabled")
        return self._ai_client

    def _save_debug_screenshot(self, png_bytes: bytes):
        """Save screenshot to storage/debug/ if debug flag is set."""
        if not png_bytes:
            return
        with get_session() as session:
            cfg = session.query(Config).filter_by(key="dashscope_debug_screenshots").first()
        if not cfg or cfg.value.lower() != "true":
            return
        import os
        os.makedirs("storage/debug", exist_ok=True)
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = f"storage/debug/ai_upgrade_{ts}.png"
        with open(filepath, "wb") as f:
            f.write(png_bytes)
        logger.debug("Debug screenshot saved: %s", filepath)
```

Note: `Config` needs to be added to the import at top of file. Add `Config` to the existing import line 16:

```python
from backend.db.models import RoiTemplate, Config, AttackLog
```

(Check — if `Config` is already imported, skip this sub-step. If line 16 only has `RoiTemplate, AttackLog`, add `Config`.)

- [ ] **Step 3: Extract template matching into _do_upgrade_execute_template**

Rename the current `_do_upgrade_execute` method (lines 451-566) to `_do_upgrade_execute_template`. Change the method signature and remove the docstring reference to "template matching" since it IS the template path:

```python
    async def _do_upgrade_execute_template(self, adb):
        """Fallback: execute upgrade using template matching (original logic)."""
```

Keep the method body exactly as-is (lines 452-566) — all the local imports, template matching, hammer detection, OCR cost, confirm button logic.

- [ ] **Step 4: Write new _do_upgrade_execute with AI-first flow**

Insert the new `_do_upgrade_execute` BEFORE `_do_upgrade_execute_template` (since it calls it as fallback). Write:

```python
    async def _do_upgrade_execute(self, adb):
        """Execute upgrade: try AI vision first, fall back to template matching."""
        from backend.db.database import get_session
        from backend.db.models import UpgradeQueue, RoiTemplate, Config
        from backend.vision.matching import match_template
        from datetime import datetime

        if not getattr(self, "_upgrade_item", None):
            logger.info("No upgrade item selected — skipping")
            return

        item = self._upgrade_item
        self.state = "UPGRADING"
        logger.info("Executing upgrade: %s lvl %d", item.name, item.target_level)

        # Step 1: Tap builder menu (from calibrated ROI)
        with get_session() as session:
            menu_roi = session.query(RoiTemplate).filter_by(roi_name="builder_menu").first()
        if not menu_roi:
            logger.warning("builder_menu ROI not calibrated")
            self._upgrade_item = None
            return
        menu_cx = menu_roi.x_pos + menu_roi.width // 2
        menu_cy = menu_roi.y_pos + menu_roi.height // 2
        await human_tap(adb, menu_cx, menu_cy, sigma=3)
        await human_delay(1.5, 2.5)

        # Step 2: Screencap
        screen = await adb.screencap()
        if not screen:
            return

        # Save debug screenshot if enabled
        self._save_debug_screenshot(screen)

        # Step 3: Try AI analysis
        client = self._get_ai_client()
        if not client.available:
            logger.warning("AI client unavailable — falling back to template matching")
            await self._do_upgrade_execute_template(adb)
            return

        ai_buildings = client.analyze_screenshot(screen)

        if ai_buildings is None:
            logger.warning("AI analysis failed — falling back to template matching")
            await self._do_upgrade_execute_template(adb)
            return

        if not ai_buildings:
            logger.info("AI: no upgradable buildings found in builder menu")
            # Close builder menu
            await human_tap(adb, menu_cx, menu_cy, sigma=3)
            await human_delay(0.3, 0.8)
            self._upgrade_item = None
            return

        # Step 4: Pick first building and tap its Upgrade button
        building = ai_buildings[0]
        logger.info("AI detected: %s at (%d, %d) cost=%d %s",
                     building["name"], building["x"], building["y"],
                     building["cost"], building["resource"])

        await human_tap(adb, building["x"], building["y"], sigma=3)
        await human_delay(0.8, 1.5)

        # Step 5: Find and tap confirm button (template matching)
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
            logger.warning("Confirm button not found after AI upgrade tap")
            self._upgrade_item = None
            return

        # Step 6: Update DB
        with get_session() as session:
            db_item = session.query(UpgradeQueue).get(item.id)
            if db_item:
                db_item.status = "IN_PROGRESS"
                db_item.started_at = datetime.utcnow()
                if building.get("cost"):
                    db_item.cost = building["cost"]
                session.commit()

        logger.info("AI upgrade started: %s (cost=%d %s)",
                     building["name"], building["cost"], building["resource"])
        self._upgrade_item = None
        await human_delay(1.0, 2.0)
```

- [ ] **Step 5: Verify syntax and import**

Run: `python -c "from backend.engine.sequence_runner import SequenceRunner; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add backend/engine/sequence_runner.py
git commit -m "feat: wire AI vision into upgrade_execute with template fallback
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

### Task 4: Final verification

**Files:**
- No file changes, verification only

- [ ] **Step 1: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All existing tests pass + new AI tests pass

- [ ] **Step 2: Verify full import chain**

Run: `python -c "from backend.main import app; print('Full import OK')"`
Expected: `Full import OK`

- [ ] **Step 3: Quick smoke test of AI module standalone**

Run: `python -c "from backend.vision.ai import DashScopeClient, _build_prompt, _parse_response; print('AI module OK')"`
Expected: `AI module OK`

- [ ] **Step 4: Commit**

```bash
git status
git commit -m "chore: final verification — all tests pass, AI vision ready
Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```
