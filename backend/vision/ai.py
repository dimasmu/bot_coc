"""DashScope AI vision client for analyzing Clash of Clans screenshots."""

import base64
import json
import logging
import re

logger = logging.getLogger(__name__)

# --- Constants ---
BASE_URL = "https://dashscope-intl.aliyuncs.com/api/v1"
MODEL = "qwen3.7-flash"
SCREEN_WIDTH = 1280
SCREEN_HEIGHT = 800  # generous bound, AI coords get capped to actual screen
AI_RESIZE = 2  # send image at half-resolution, scale coords back by 2x
VALID_RESOURCES = {"gold", "elixir", "dark_elixir"}
MAX_BUILDINGS = 5


def _build_prompt() -> str:
    """Build the strict JSON-output prompt for building/button detection."""
    return """You are a Clash of Clans bot assistant. Analyze this Clash of Clans screenshot (1280x720).

This is the Builder Suggestions menu after pressing the builder button.
Find all buildings that have an "Upgrade" button (green text showing a cost like "1,000,000" or similar).

For each upgradable building, provide the EXACT pixel coordinates of its
UPGRADE BUTTON (the green button with the cost text). This is what we need
to click to start the upgrade.

Return ONLY valid JSON (no markdown, no explanation):

{
  "buildings": [
    {"name": "Archer Tower", "x": 450, "y": 320, "cost": 800000, "resource": "gold"}
  ]
}

If nothing is upgradable: {"buildings": []}

IMPORTANT RULES:
- x and y MUST be integers in range 0-1279 and 0-749
- x,y must point to the CENTER of the green Upgrade button (not the building name)
- cost MUST be an integer (no commas)
- resource: "gold", "elixir", or "dark_elixir" """


MENU_PROMPT = """You are a Clash of Clans bot assistant. Analyze this screenshot (1280x720).
This is the Builder Suggestions menu.

Find the first building listed under "Suggested Upgrades".
For this building, provide the pixel coordinates of its BUILDING ROW
(the area with the building name and icon on the LEFT side of the row).
We will click this row to select the building. After closing the menu,
the upgrade hammer will appear at the building's location on the base.

Return ONLY valid JSON:
{
  "buildings": [
    {"name": "Archer Tower", "x": 440, "y": 385, "cost": 800000, "resource": "gold"}
  ]
}

RULES:
- x,y must be integers in range 0-1279 and 0-749
- x,y should point to the row center (building name area, left side of list)
- cost must be integer (no commas)
- resource: "gold", "elixir", or "dark_elixir" """


BASE_PROMPT = """You are a Clash of Clans bot assistant. Analyze this screenshot (1280x720).
A building was just selected. Find the UPGRADE BUTTON visible on screen.
It may be:
- A hammer icon near the building
- A cogwheel/gear icon
- A floating upgrade panel with cost text and a resource icon

Scan the ENTIRE screen and return the pixel coordinates of the upgrade button.

Return ONLY valid JSON:
{
  "buildings": [
    {"name": "UpgradeButton", "x": 500, "y": 400, "cost": 0, "resource": "gold"}
  ]
}

RULES:
- x,y must be integers in range 0-1279 and 0-749
- Point to the EXACT center of the clickable upgrade button/panel"""


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
        if x < 0 or x >= SCREEN_WIDTH or y < 0 or y >= SCREEN_HEIGHT:
            # Cap to screen bounds instead of skipping
            x = max(0, min(x, SCREEN_WIDTH - 1))
            y = max(0, min(y, min(SCREEN_HEIGHT - 1, 719)))  # actual screen is 720
            logger.warning("Building '%s' coords capped: (%d, %d)", name, x, y)

        cost = b.get("cost", 0)
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            cost = int(cost)
            if cost < 0:
                logger.warning("Building '%s' has negative cost %d, skipping", name, cost)
                continue
        else:
            cost = 0

        resource = b.get("resource", "").replace("dark-elixir", "dark_elixir").replace("-", "_")
        if resource not in VALID_RESOURCES:
            logger.warning("Unknown resource '%s', defaulting to 'gold'", resource)
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
        try:
            import dashscope
            dashscope.base_http_api_url = BASE_URL
        except ImportError:
            pass

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def analyze_screenshot(self, png_bytes: bytes, prompt_override: str | None = None) -> list[dict] | None:
        """Analyze a builder menu screenshot and return upgradable buildings.

        Args:
            png_bytes: Raw PNG image bytes (1280x720).
            prompt_override: Optional custom prompt. Uses default if None.

        Returns:
            List of building dicts, empty list for no upgrades, None on failure.
        """
        prompt = prompt_override if prompt_override else self._prompt
        if not self.available:
            logger.warning("DashScope API key not configured")
            return None

        try:
            import dashscope
        except ImportError:
            logger.error("dashscope package not installed")
            return None

        import io
        from PIL import Image
        img = Image.open(io.BytesIO(png_bytes))
        logger.info("Screenshot: %dx%d", img.width, img.height)

        # Resize to half resolution to reduce API latency
        # (1280x720 → 640x360, ~1.3MB → ~100KB)
        img = img.resize((img.width // 2, img.height // 2), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        image_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        messages = [{
            "role": "user",
            "content": [
                {"image": f"data:image/png;base64,{image_b64}"},
                {"text": prompt},
            ]
        }]

        try:
            logger.info("AI calling DashScope (qwen3.7-flash)...")
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
        result = _parse_response(text)
        if result is not None:
            # Scale coordinates from resized image back to 1280x720
            for b in result:
                b["x"] *= AI_RESIZE
                b["y"] *= AI_RESIZE
        return result
