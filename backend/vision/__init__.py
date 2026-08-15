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

def _confirm_cost_is_red(image: np.ndarray,
                         box: Tuple[int, int, int, int]) -> bool:
    """True if the confirm button at `box` contains red cost digits.

    Red digits on the confirm button mean insufficient resources — the
    button body stays green but the game renders the cost in red.
    """
    x, y, bw, bh = box
    x1 = max(0, x)
    y1 = max(0, y)
    x2 = min(image.shape[1], x + bw)
    y2 = min(image.shape[0], y + bh)
    if x2 <= x1 or y2 <= y1:
        return False

    hsv = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    red = ((hsv[:, :, 0] < 12) | (hsv[:, :, 0] > 168)) & \
          (hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 90)
    area = (x2 - x1) * (y2 - y1)
    return int(red.sum()) > area * 0.03


def analyze_upgrade_confirm_button(image):
    """Analisis tombol hijau konfirmasi pada modal UPGRADE (lab & bangunan).

    Tombol hijau "Research"/"Upgrade" kedua jenis modal berada di
    kanan-bawah (terukur konsisten: box (797,587,201x85), pusat
    (897,629)). Deteksi HSV langsung di ROI kanan-bawah yang ketat —
    template matcher LAMA salah posisi di kedua modal (match lemah
    0.51 di (986,547) → tap meleset).

    Returns:
        - ("READY", (cx, cy)): tombol hijau, resource cukup.
        - ("INSUFFICIENT_RESOURCES", None): angka biaya merah di dalam tombol.
        - ("NOT_FOUND", None): tidak ada tombol hijau di ROI (termasuk
          tombol abu-abu Town Hall required — tidak bisa dibedakan andal
          karena tombol Hammer of Fighting juga abu-abu di ROI ini).
    """
    h, w = image.shape[:2]
    x1, y1 = int(w * 0.55), int(h * 0.75)
    x2, y2 = int(w * 0.95), h

    roi = image[y1:y2, x1:x2]
    hsv_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

    lower_green = np.array([35, 100, 100])
    upper_green = np.array([85, 255, 255])
    green_mask = cv2.inRange(hsv_roi, lower_green, upper_green)

    contours, _ = cv2.findContours(
        green_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
    )
    # Tombol confirm hijau punya ukuran/aspect khas: area 10k-25k px,
    # aspect 1.6-2.8 (terukur 16,616 px / 2.36 konsisten). Ini menyaring:
    # ikon kecil, tombol "Finish Now" (8.4k, panel in-progress setelah
    # upgrade dimulai), dan blob rumput yang menyatu (42k / aspect 3.1).
    wide = [c for c in contours
            if 10_000 <= cv2.contourArea(c) <= 25_000
            and 1.6 <= cv2.boundingRect(c)[2] / max(cv2.boundingRect(c)[3], 1)
            <= 2.8]
    if not wide:
        return ("NOT_FOUND", None)

    best = max(wide, key=cv2.contourArea)
    bx, by, bw, bh = cv2.boundingRect(best)
    box = (bx + x1, by + y1, bw, bh)
    cx = bx + bw // 2 + x1
    cy = by + bh // 2 + y1

    if _confirm_cost_is_red(image, box):
        return ("INSUFFICIENT_RESOURCES", None)
    return ("READY", (cx, cy))


