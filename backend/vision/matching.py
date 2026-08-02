"""OpenCV template matching utilities."""

import logging
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def match_template(screenshot: bytes, template_path: str, threshold: float = 0.8) -> tuple[int, int] | None:
    """Find a template in a screenshot. Supports transparent PNGs via alpha mask.
    Returns (x, y) center or None."""
    path = Path(template_path)
    if not path.exists():
        logger.warning("Template not found: %s", template_path)
        return None

    # Load template with alpha channel if present
    tpl_color = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if tpl_color is None:
        return None

    has_alpha = len(tpl_color.shape) == 3 and tpl_color.shape[2] == 4
    mask = None
    if has_alpha:
        # Use alpha channel as mask: 0 = transparent (ignore), 255 = opaque (match)
        mask = tpl_color[:, :, 3]
        tpl_gray = cv2.cvtColor(tpl_color[:, :, :3], cv2.COLOR_BGR2GRAY)
    else:
        tpl_gray = cv2.cvtColor(tpl_color, cv2.COLOR_BGR2GRAY) if len(tpl_color.shape) == 3 else tpl_color

    nparr = np.frombuffer(screenshot, np.uint8)
    screen = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
    h, w = tpl_gray.shape

    if has_alpha and mask is not None:
        # Use masked matching for transparent templates
        result = cv2.matchTemplate(screen, tpl_gray, cv2.TM_CCOEFF_NORMED, mask=mask)
    else:
        result = cv2.matchTemplate(screen, tpl_gray, cv2.TM_CCOEFF_NORMED)

    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val < threshold:
        return None

    center_x = max_loc[0] + w // 2
    center_y = max_loc[1] + h // 2
    logger.debug("Matched %s (val=%.3f) at (%d,%d)", template_path, max_val, center_x, center_y)
    return (center_x, center_y)


def find_template_in_roi(
    screenshot: bytes,
    x: int,
    y: int,
    width: int,
    height: int,
    threshold: float = 0.8,
) -> tuple[int, int] | None:
    """Find a template within a specific ROI of the screenshot.

    Uses contour detection to find non-black pixel clusters (button detection).
    Returns (center_x, center_y) or None.
    """
    nparr = np.frombuffer(screenshot, np.uint8)
    screen = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
    h, w = screen.shape

    # Clamp ROI to screen bounds
    x1 = max(0, x)
    y1 = max(0, y)
    x2 = min(w, x + width)
    y2 = min(h, y + height)

    if x2 <= x1 or y2 <= y1:
        return None

    roi = screen[y1:y2, x1:x2]

    # Look for significant clusters of non-black pixels (button detection)
    _, thresh = cv2.threshold(roi, 100, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        return None

    largest = max(contours, key=cv2.contourArea)
    M = cv2.moments(largest)
    if M["m00"] == 0:
        return None

    cx = int(M["m10"] / M["m00"]) + x1
    cy = int(M["m01"] / M["m00"]) + y1
    return (cx, cy)
