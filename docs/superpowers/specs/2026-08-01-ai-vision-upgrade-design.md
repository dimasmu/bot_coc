# AI Vision — Smart Upgrade Detection via DashScope

**Status:** Draft
**Date:** 2026-08-01

## Problem

Current `_do_upgrade_execute` uses template matching (`match_template` via OpenCV `TM_CCOEFF_NORMED`) to find UI elements on the builder menu screen. Template matching is brittle:

- Breaks when CoC UI changes (seasonal updates, minor layout shifts)
- Requires exact template screenshots per device/resolution
- Uses hardcoded pixel offsets ("tap 60px below suggestion label", "cost is 45px below hammer center")
- No semantic understanding of what is on screen

## Goal

Replace template matching with DashScope qwen3.7-flash multimodal vision to detect upgradeable buildings. The AI analyzes a screenshot of the builder menu and returns structured JSON with building names, coordinates, and costs.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ SequenceRunner._do_upgrade_execute (modified)               │
│                                                             │
│  1. Tap builder_menu ROI                                    │
│  2. screencap()                                             │
│  3. DashScopeClient.analyze_screenshot(png_bytes)           │
│     ├── SUCCESS → parse JSON, tap coords, confirm          │
│     └── FAILURE → fallback to template matching (existing)  │
│  4. Update DB: status=IN_PROGRESS, cost, started_at         │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ backend/vision/ai.py (NEW)                                  │
│                                                             │
│  class DashScopeClient:                                     │
│    - __init__(api_key)                                      │
│    - _build_prompt()        → strict JSON prompt            │
│    - analyze_screenshot(png)→ list[{name,x,y,cost,resource}] │
│    - _parse_response(raw)   → validated list or None        │
│                                                             │
│  Uses: dashscope.MultiModalConversation.call(               │
│    model='qwen3.7-flash',                                    │
│    messages=[{"role":"user","content":[image, prompt_text]}] │
│  )                                                          │
└─────────────────────────────────────────────────────────────┘
```

## Components

### 1. `backend/vision/ai.py` (NEW)

DashScope wrapper. Single responsibility: send PNG screenshot, return parsed result.

```python
class DashScopeClient:
    BASE_URL = "https://dashscope-intl.aliyuncs.com/api/v1"
    MODEL = "qwen3.7-flash"
    TIMEOUT = 10  # seconds

    def analyze_screenshot(png: bytes) -> list[dict] | None:
        """
        Returns list of upgradable buildings or None on failure.
        Each dict: {"name": str, "x": int, "y": int, "cost": int, "resource": str}
        Coordinates are absolute pixel positions in 1280x720.
        """
```

#### Prompt (strict JSON output)

```
You are a Clash of Clans bot assistant. Analyze this screenshot (1280x720).
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
- resource: "gold", "elixir", or "dark_elixir"
```

#### Response Validation

```
_parse_response(raw_text: str) → list[dict] | None:
  1. Strip ```json fences if present
  2. json.loads()
  3. Validate "buildings" key exists and is a list
  4. For each building:
     - "name" is non-empty string
     - "x" is int, 0 <= x <= 1279
     - "y" is int, 0 <= y <= 719
     - "cost" is int >= 0 (if present)
     - "resource" in {"gold", "elixir", "dark_elixir"} (if present)
  5. Return list or None (on validation failure: log raw response, return None)
```

### 2. `backend/engine/sequence_runner.py` (EDIT)

Modify `_do_upgrade_execute()`:

**Before** (current ~170 lines of template matching + hardcoded offsets):
- Template match `btn_upgrade_suggestion.png` → tap below
- Template match `btn_upgrade_hammer.png` → OCR cost at fixed offset
- Template match `btn_upgrade_confirm_1.png` / `btn_upgrade_confirm_2.png` → tap

**After:**
```
_do_upgrade_execute(adb):
  1. Tap builder_menu ROI (existing)
  2. delay 1.5s
  3. screenshot = await adb.screencap()
  4. Save debug screenshot to storage/debug/ if DEBUG_SCREENSHOTS config set
  5. ai_buildings = dashscope_client.analyze_screenshot(screenshot)
  6. IF ai_buildings is None:
       → log warn, FALLBACK to template matching (existing code preserved)
  7. IF ai_buildings is empty:
       → log info "no upgrades available", return
  8. Pick first building from ai_buildings
  9. human_tap(adb, building.x, building.y)
  10. delay 0.3s
  11. Tap btn_upgrade (existing ROI)
  12. Tap btn_confirm (existing ROI)
  13. Update DB: status=IN_PROGRESS, cost=building.cost, started_at
  14. Log: "Upgraded {name} (cost={cost} {resource})"
```

Existing template matching code is preserved as fallback path (not deleted).

### 3. `backend/db/models.py` (EDIT — optional)

Config keys used:
- `dashscope_api_key` — stored via existing Config model

### 4. `pyproject.toml` (EDIT)

Add dependency: `dashscope`

## Error Handling

| Scenario | Handling |
|----------|----------|
| API key not configured | Log warn, fallback to template matching |
| DashScope timeout (10s) | Retry 1x, then fallback |
| DashScope returns error/HTML | Fallback |
| JSON unparseable | Log raw response to debug, fallback |
| Coordinates out of bounds | Filter invalid buildings, use remaining |
| AI returns `buildings: []` | Log: "no upgrades available", return |
| AI returns >5 buildings | Cap to 5, process one per cycle |
| Tap building but no upgrade screen | Timeout 3s, skip, try next |
| Adb screencap fails | Return early |

## Feature Flags / Config

| Key | Default | Description |
|-----|---------|-------------|
| `dashscope_api_key` | `""` | DashScope API key (empty = AI disabled, fallback only) |
| `dashscope_debug_screenshots` | `"false"` | Save screenshots sent to AI under `storage/debug/` |

## Testing

### Unit: `tests/test_ai_upgrade.py`
- `test_parse_valid_json()` — valid response parses correctly
- `test_parse_invalid_json()` — malformed JSON returns None
- `test_parse_empty_buildings()` — `{"buildings":[]}` returns empty list
- `test_parse_out_of_bounds_coords()` — invalid coords filtered out
- `test_parse_markdown_wrapped_json()` — ``` fences stripped
- `test_build_prompt_structure()` — prompt contains required keywords

### Integration (manual, requires API key)
- Script: `python -m backend.vision.ai --test <screenshot.png>` — prints parsed result

## Files Changed

| File | Action | Description |
|------|--------|-------------|
| `backend/vision/ai.py` | **CREATE** | DashScope client wrapper |
| `backend/engine/sequence_runner.py` | **EDIT** | `_do_upgrade_execute` — replace template matching with AI + fallback |
| `pyproject.toml` | **EDIT** | Add `dashscope` dependency |
| `tests/test_ai_upgrade.py` | **CREATE** | Unit tests for AI client |

## Out of Scope (v1)

- Lab/hero upgrades (building only)
- Multi-upgrade per cycle (one per cycle)
- Cost predictive analysis or optimization suggestions
- Builder management (manually assigning builders)
- Rate limiting or cost tracking dashboard
