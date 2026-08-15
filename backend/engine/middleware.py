"""Screen blocker detection and resolution for Clash of Clans.

Detects and dismisses popups/events/modals that block the main game screen:
- CONTINUE button (green) on event reward popups
- Return Home button (orange) after battles
- Close X button (red) in sub-menus
- Event animation screens (dark wood background)
"""

import cv2
import numpy as np


def resolve_blockers(screenshot: np.ndarray) -> tuple[bool, tuple[int, int] | None]:
    """Detect and return coordinates for the highest-priority screen blocker.

    Checks in priority order: Return Home → Continue → Event animation → Close X.

    Args:
        screenshot: BGR image as numpy array (from cv2.imdecode).

    Returns:
        (is_blocked, tap_coordinates) — tap_coordinates is None if no blocker found.
    """
    hsv = cv2.cvtColor(screenshot, cv2.COLOR_BGR2HSV)

    # Case 1: End of Battle — Return Home button (orange, bottom-center)
    pos = _find_return_home_button(hsv)
    if pos:
        return True, pos

    # Case 2: End of Battle — Claim Reward button (green, bottom-center)
    pos = _find_claim_reward_button(hsv)
    if pos:
        return True, pos

    # Case 3: Event Reward — CONTINUE button (green, bottom-center)
    pos = _find_continue_button(hsv)
    if pos:
        return True, pos

    # Case 4: Event animation screen (dark wood, no button yet)
    if _is_event_animation_screen(hsv):
        return True, (640, 360)  # tap center to advance animation

    # Case 5: Sub-menu / popup — Close X button (red, top-right)
    pos = _find_close_x_button(hsv)
    if pos:
        return True, pos

    return False, None


# ── private detectors ────────────────────────────────────────────────


def _find_return_home_button(hsv: np.ndarray) -> tuple[int, int] | None:
    """Find the orange Return Home button after a battle ends."""
    h, w = hsv.shape[:2]
    roi = np.zeros((h, w), dtype=np.uint8)
    roi[520:680, 300:900] = 255

    lower = np.array([10, 160, 160])
    upper = np.array([25, 255, 255])
    mask = cv2.inRange(hsv, lower, upper)
    mask = cv2.bitwise_and(mask, mask, mask=roi)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if 3000 < area < 20000:
            x, y, bw, bh = cv2.boundingRect(cnt)
            if bw / bh > 1.8:  # wide rectangular button
                return (x + bw // 2, y + bh // 2)
    return None


def _find_continue_button(hsv: np.ndarray) -> tuple[int, int] | None:
    """Find the green CONTINUE button on event reward popups."""
    h, w = hsv.shape[:2]
    roi = np.zeros((h, w), dtype=np.uint8)
    roi[550:700, 400:880] = 255

    lower = np.array([35, 120, 150])
    upper = np.array([85, 255, 255])
    mask = cv2.inRange(hsv, lower, upper)
    mask = cv2.bitwise_and(mask, mask, mask=roi)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for cnt in contours:
        if 2000 < cv2.contourArea(cnt) < 15000:
            x, y, bw, bh = cv2.boundingRect(cnt)
            return (x + bw // 2, y + bh // 2)
    return None


def _find_claim_reward_button(hsv: np.ndarray) -> tuple[int, int] | None:
    """Find the green Claim Reward button at the bottom-center of battle-end screen.

    Uses morphological closing to fill holes caused by red ticket icon
    and white text inside the button, which fragment the green mask.
    """
    h, w = hsv.shape[:2]
    roi = np.zeros((h, w), dtype=np.uint8)
    roi[600:710, 350:850] = 255

    # Broader HSV range for the claim button green
    lower = np.array([25, 80, 100])
    upper = np.array([85, 255, 255])
    mask = cv2.inRange(hsv, lower, upper)
    mask = cv2.bitwise_and(mask, mask, mask=roi)

    # Morphological closing: fill holes from white text and red ticket icon
    kernel = np.ones((15, 15), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < 1500 or area > 25000:
            continue
        x, y, bw, bh = cv2.boundingRect(cnt)
        if bw / bh > 1.4:  # wide horizontal button
            return (x + bw // 2, y + bh // 2)
    return None


def _is_event_animation_screen(hsv: np.ndarray) -> bool:
    """Detect the dark wood background characteristic of event screens."""
    sample = hsv[200:500, 50:150]
    lower = np.array([8, 60, 40])
    upper = np.array([28, 220, 180])
    mask = cv2.inRange(sample, lower, upper)
    return np.count_nonzero(mask) / sample.size > 0.40


def _find_close_x_button(hsv: np.ndarray) -> tuple[int, int] | None:
    """Find the red X close button in the top-right corner of popup windows.

    Restricted to a narrow ROI to avoid false positives on gem/coin UI.
    """
    h, w = hsv.shape[:2]
    roi = np.zeros((h, w), dtype=np.uint8)
    roi[20:80, 1100:1250] = 255  # very top-right only — avoids gem bar

    lower_r1 = np.array([0, 150, 150])
    upper_r1 = np.array([10, 255, 255])
    lower_r2 = np.array([170, 150, 150])
    upper_r2 = np.array([180, 255, 255])
    mask = cv2.inRange(hsv, lower_r1, upper_r1) | cv2.inRange(hsv, lower_r2, upper_r2)
    mask = cv2.bitwise_and(mask, mask, mask=roi)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for cnt in contours:
        if 50 < cv2.contourArea(cnt) < 2500:
            x, y, bw, bh = cv2.boundingRect(cnt)
            return (x + bw // 2, y + bh // 2)
    return None


def _modal_is_open(image: np.ndarray) -> bool:
    """Detect an open modal/panel via the dark semi-transparent dim overlay.

    When a modal is open the game dims everything outside the panel, so
    the screen edge strips become much darker than the panel interior.
    The panel-vs-edge contrast works regardless of the village day/night
    cycle. Validated on real screenshots: open modal 123-133, clean home
    (day and night) <= 49.
    """
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2].astype(np.float64)

    panel = v[200:400, 400:850].mean()
    edge_l = v[250:450, 0:100].mean()
    edge_r = v[250:450, 1200:1280].mean()
    # min() of the two edges so a passing cloud shadow on one side
    # doesn't fake the contrast.
    return bool(panel - min(edge_l, edge_r) > 100)


def find_shop_arrow_cv(image: np.ndarray) -> tuple[int, int] | None:
    """Find the orange arrow indicator on the highlighted Shop card.

    Replaces the slow DashScope AI call. The arrow is orange with high
    saturation against the blue/gray shop card background.

    Args:
        image: BGR image as numpy array.

    Returns:
        (center_x, center_y) of the arrow, or None if not found.
    """
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

    # Orange arrow: H 15-35, S >= 120, V >= 150
    lower = np.array([15, 120, 150])
    upper = np.array([35, 255, 255])
    mask = cv2.inRange(hsv, lower, upper)

    # Clean noise
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best = None
    best_area = 0
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < 1500 or area > 10000:
            continue
        x, y, bw, bh = cv2.boundingRect(cnt)
        cy = y + bh // 2
        if cy < 150 or cy > 600:  # must be in middle area
            continue
        if area > best_area:
            best_area = area
            best = (x + bw // 2, y + bh // 2)

    return best
