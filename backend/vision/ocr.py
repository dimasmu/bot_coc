"""OCR utilities using EasyOCR for reading numbers from game screen."""

import logging
import os
import re

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_reader = None


def _get_reader():
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        logger.info("EasyOCR initialized")
    return _reader


# Padding config — tuned per user with diagnostic images
_PADDING = {
    "gold_number":   (2, 80, -4, 4),
    "elixir_number": (2, 80, 0, 4),
}


def _get_padding(roi_name):
    if roi_name in _PADDING:
        return _PADDING[roi_name]
    if roi_name.endswith("_number"):
        return (2, 80, 0, 4)
    if roi_name:
        return (8, 8, 8, 8)
    return (0, 0, 0, 0)


def read_number(screenshot: bytes, x: int, y: int, width: int, height: int,
                roi_name: str = "") -> int | None:
    """Read a number from a region using EasyOCR."""
    try:
        nparr = np.frombuffer(screenshot, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        h, w = img.shape[:2]

        pad_left, pad_right, pad_top, pad_bottom = _get_padding(roi_name)
        x1 = max(0, x - pad_left)
        y1 = max(0, y - pad_top)
        x2 = min(w, x + width + pad_right)
        y2 = min(h, y + height + pad_bottom)

        if x2 <= x1 or y2 <= y1:
            return None

        roi = img[y1:y2, x1:x2]
        # Scale up 2x for small text
        roi = cv2.resize(roi, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
        # Convert to grayscale — color backgrounds (yellow gold, pink elixir)
        # can confuse OCR due to low text-to-background contrast
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

        reader = _get_reader()
        results = reader.readtext(gray, detail=0)
        if results:
            logger.info("EasyOCR %s raw: %r", roi_name or "generic", results)

        # Extract the first valid digit string from the raw results
        primary_text = ""
        for r in results:
            t = re.sub(r"\D", "", r)
            if t:
                primary_text = t
                break

        # Fallback: if the raw result is suspiciously short (< 4 digits),
        # re-read with OTSU+erode. This helps on gradient backgrounds like
        # the gold bar at low TH where the leftmost digit has poor contrast.
        if len(primary_text) < 4:
            _, binary = cv2.threshold(gray, 0, 255,
                                       cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
            eroded = cv2.erode(binary, kernel, iterations=1)
            results2 = reader.readtext(eroded, detail=0)
            if results2:
                logger.info("EasyOCR %s otsu fallback: %r",
                            roi_name or "generic", results2)
            for r in results2:
                t = re.sub(r"\D", "", r)
                if t and len(t) > len(primary_text):
                    primary_text = t
                    break

        if primary_text:
            val = int(primary_text)
            logger.info("EasyOCR %s → %d (text=%r)", roi_name or "generic",
                        val, primary_text)
            return val

        return None
    except Exception as e:
        logger.error("EasyOCR read_number failed: %s", e)
        return None


def read_ratio(screenshot: bytes, x: int, y: int, width: int, height: int,
               roi_name: str = "") -> tuple[int, int] | None:
    """Read a 'used/total' ratio (e.g. '0/1', '2/5') from a region.

    Uses the same grayscale + 2x + OTSU + erode fallback as read_number,
    then falls back to reading the left/right halves separately.
    Returns (used, total) or None if unreadable.
    """
    try:
        nparr = np.frombuffer(screenshot, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        h, w = img.shape[:2]

        pad_left, pad_right, pad_top, pad_bottom = _get_padding(roi_name)
        x1 = max(0, x - pad_left)
        y1 = max(0, y - pad_top)
        x2 = min(w, x + width + pad_right)
        y2 = min(h, y + height + pad_bottom)

        if x2 <= x1 or y2 <= y1:
            return None

        roi = img[y1:y2, x1:x2]
        roi = cv2.resize(roi, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

        reader = _get_reader()

        def _extract(text_results) -> tuple[int, int] | None:
            text = " ".join(text_results)
            m = re.search(r'(\d+)\s*/\s*(\d+)', text)
            if m:
                return int(m.group(1)), int(m.group(2))
            return None

        # Pass 1: grayscale full-text OCR
        results = reader.readtext(gray, detail=0, paragraph=True)
        pair = _extract(results)
        if pair:
            logger.info("read_ratio %s pass1: %r -> %s", roi_name or "generic", results, pair)
            return pair

        # Pass 2: OTSU + erode fallback
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
        eroded = cv2.erode(binary, kernel, iterations=1)
        results2 = reader.readtext(eroded, detail=0, paragraph=True)
        pair = _extract(results2)
        if pair:
            logger.info("read_ratio %s pass2 (otsu): %r -> %s", roi_name or "generic", results2, pair)
            return pair

        # Pass 3: split ROI into halves and read each number separately
        left = read_number(screenshot, x, y, width // 2, height)
        right = read_number(screenshot, x + width // 2, y, width - width // 2, height)
        if left is not None and right is not None:
            logger.info("read_ratio %s split: %d/%d", roi_name or "generic", left, right)
            return (left, right)

        return None
    except Exception as e:
        logger.error("read_ratio failed: %s", e)
        return None


def read_raw_text(screenshot: bytes, x: int, y: int, width: int, height: int) -> str:
    """Read raw text from a region using EasyOCR (no digit filter).

    Performs the same preprocessing as read_number() but returns the full
    OCR text without restricting to digits. Used for reading formatted
    labels like builder count "2/5".
    """
    try:
        nparr = np.frombuffer(screenshot, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        h, w = img.shape[:2]

        x1 = max(0, x)
        y1 = max(0, y)
        x2 = min(w, x + width)
        y2 = min(h, y + height)

        if x2 <= x1 or y2 <= y1:
            return ""

        roi = img[y1:y2, x1:x2]
        roi = cv2.resize(roi, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)

        reader = _get_reader()
        results = reader.readtext(roi, detail=0, paragraph=True)
        text = " ".join(results).strip() if results else ""
        logger.debug("EasyOCR raw text: '%s'", text)
        return text
    except Exception as e:
        logger.error("EasyOCR read_raw_text failed: %s", e)
        return ""


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


def find_text(screenshot: bytes, keyword: str) -> tuple[int, int] | None:
    """Find a keyword in the screenshot using EasyOCR. Returns (cx, cy) or None.

    Args:
        screenshot: PNG bytes from adb screencap
        keyword: Text to search for (case-insensitive partial match)

    Returns:
        Center pixel coordinates of the found text bounding box, or None.
    """
    try:
        nparr = np.frombuffer(screenshot, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        h, w = img.shape[:2]

        reader = _get_reader()
        results = reader.readtext(img, detail=1, paragraph=False)
        keyword_lower = keyword.lower()
        for bbox, text, conf in results:
            if keyword_lower in text.lower():
                xs = [p[0] for p in bbox]
                ys = [p[1] for p in bbox]
                cx = int(sum(xs) / len(xs))
                cy = int(sum(ys) / len(ys))
                logger.info("Found '%s' at (%d,%d) conf=%.2f", text, cx, cy, conf)
                return (cx, cy)
        logger.debug("Text '%s' not found with OCR", keyword)
        return None
    except Exception as e:
        logger.error("find_text failed: %s", e)
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
