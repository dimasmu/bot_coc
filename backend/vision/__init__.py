"""Computer vision layer for template matching and OCR."""

import cv2
import numpy as np
from typing import Optional, Tuple, List


def match_template(image: np.ndarray, template_path: str, threshold: float = 0.7) -> Optional[Tuple[int, int]]:
    """Match template in image and return position if found above threshold.
    
    Args:
        image: Source image
        template_path: Path to template image
        threshold: Matching threshold (0-1)
    
    Returns:
        Tuple of (x, y) position if found, None otherwise
    """
    # Read template image
    template = cv2.imread(template_path)
    if template is None:
        print(f"[ERROR] Template not found: {template_path}")
        return None
    
    # Convert to grayscale for matching
    gray_img = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray_tpl = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    
    # Perform template matching
    result = cv2.matchTemplate(gray_img, gray_tpl, cv2.TM_CCOEFF_NORMED)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
    
    if max_val >= threshold:
        # Return center position
        h, w = template.shape[:2]
        center_x = max_loc[0] + w // 2
        center_y = max_loc[1] + h // 2
        return (center_x, center_y)
    
    return None

def analyze_confirm_button(image):
    """Menganalisis status tombol CONFIRM pada modal Upgrade.

    Detection priority:
      1. Template matching (lowered threshold 0.45)
      2. HSV green button detection (fallback)
      2.5. Grey button detection (Town Hall required)
      3. Red cost numbers (insufficient resources)

    Returns:
        - ("READY", (center_x, center_y)): Resource CUKUP, tombol siap diklik.
        - ("INSUFFICIENT_RESOURCES", None): Resource KURANG (Angka biaya
        berwarna MERAH).
        - ("TOWN_HALL_REQUIRED", None): Tombol abu-abu — upgrade butuh
        Town Hall level lebih tinggi.
        - ("NOT_FOUND", None): Tombol CONFIRM tidak ditemukan di layar.
    """
    h, w = image.shape[:2]

    # ── Method 1: Template matching (lowered threshold) ──
    confirm_templates = [
        "storage/templates/btn_upgrade_confirm_1.png",
        "storage/templates/btn_upgrade_confirm_2.png"
    ]

    best_score = 0
    best_pos = None
    for template_path in confirm_templates:
        tpl = cv2.imread(template_path)
        if tpl is None:
            continue
        gray_img = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray_tpl = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
        result = cv2.matchTemplate(gray_img, gray_tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        th, tw = tpl.shape[:2]
        pos = (max_loc[0] + tw // 2, max_loc[1] + th // 2)

        print(f"[CV CHECK] Template {template_path}: score={max_val:.4f} at {pos}")

        if max_val > best_score:
            best_score = max_val
            best_pos = pos

    if best_score >= 0.50:
        # Sanity check: genuine confirm buttons are never in the left 30%.
        # False positives from bright UI elements (elixir icons, etc.) often
        # match at x < 300 with similar scores to real confirm buttons (0.50-0.53).
        if best_pos[0] < w * 0.3:
            print(f"[CV CHECK] Template match rejected — X={best_pos[0]} < {int(w*0.3)} (false positive)")
        else:
            print(f"[CV CHECK] Template match: score={best_score:.4f} at {best_pos} -> READY")
            return ("READY", best_pos)

    # ── Method 2: HSV green button detection (fallback) ──
    # ROI: bottom 40%, right 75% of screen.
    # Lab confirm buttons appear near center (x~500); building buttons
    # at bottom-right (x~900). Wider ROI covers both.
    roi_x1 = int(w * 0.25)
    roi_y1 = int(h * 0.60)
    roi_x2 = w
    roi_y2 = h
    roi = image[roi_y1:roi_y2, roi_x1:roi_x2]
    hsv_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

    # Green range: bright greens found in CoC confirm/upgrade buttons
    lower_green = np.array([35, 100, 100])
    upper_green = np.array([85, 255, 255])
    green_mask = cv2.inRange(hsv_roi, lower_green, upper_green)

    # Find green contours in ROI
    contours, _ = cv2.findContours(
        green_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
    )
    green_contours = [c for c in contours if cv2.contourArea(c) > 200]
    total_green = cv2.countNonZero(green_mask)

    print(f"[CV CHECK] HSV green: {len(green_contours)} contours, {total_green} px in ROI")

    if green_contours:
        # Take largest green contour as confirm button
        best_cnt = max(green_contours, key=cv2.contourArea)
        bx, by, bw, bh = cv2.boundingRect(best_cnt)
        cx = bx + bw // 2 + roi_x1
        cy = by + bh // 2 + roi_y1
        area = cv2.contourArea(best_cnt)
        print(f"[CV CHECK] HSV green button: area={area:.0f} at ({cx},{cy}) -> READY")
        return ("READY", (cx, cy))

    # ── Method 2.5: Grey (disabled) button detection ──
    # A grey confirm button means: upgrade requires higher Town Hall level.
    # Detection: low saturation in the same ROI as green button search.
    grey_lower = np.array([0, 0, 100])
    grey_upper = np.array([180, 50, 255])
    grey_mask = cv2.inRange(hsv_roi, grey_lower, grey_upper)
    grey_contours, _ = cv2.findContours(
        grey_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
    )
    grey_contours = [c for c in grey_contours if cv2.contourArea(c) > 200]
    total_grey = cv2.countNonZero(grey_mask)

    if grey_contours and total_grey > 500:
        print(f"[CV CHECK] Grey disabled button: {len(grey_contours)} contours, "
              f"{total_grey} px -> TOWN_HALL_REQUIRED")
        return ("TOWN_HALL_REQUIRED", None)

    # ── Method 3: Red cost number detection (insufficient resources) ──
    y1, y2 = int(h * 0.78), int(h * 0.84)
    x1, x2 = int(w * 0.45), int(w * 0.55)

    cost_crop = image[y1:y2, x1:x2]
    hsv_cost = cv2.cvtColor(cost_crop, cv2.COLOR_BGR2HSV)

    lower_red1 = np.array([0, 130, 130])
    upper_red1 = np.array([10, 255, 255])
    lower_red2 = np.array([170, 130, 130])
    upper_red2 = np.array([180, 255, 255])

    red_mask = cv2.inRange(hsv_cost, lower_red1, upper_red1) + cv2.inRange(
        hsv_cost, lower_red2, upper_red2
    )

    red_pixels = cv2.countNonZero(red_mask)

    if red_pixels > 30:
        print(f"[CV CHECK] Angka biaya berwarna MERAH ({red_pixels} px) -> Resource tidak cukup!")
        return ("INSUFFICIENT_RESOURCES", None)

    print(f"[CV CHECK] NOT_FOUND (best tpl={best_score:.4f}, green px={total_green}, red px={red_pixels})")
    return ("NOT_FOUND", None)