"""REST endpoints for ROI template management and screenshot capture."""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import pytesseract
from PIL import Image

from backend.adb.manager import adb_manager
from backend.db.database import get_session
from backend.db.models import RoiTemplate
from backend.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")


# --- Screenshot ---

@router.get("/screenshot")
async def get_screenshot():
    """Get a single screenshot from the emulator. Returns PNG bytes."""
    if not adb_manager.is_connected:
        raise HTTPException(status_code=503, detail="ADB not connected")
    frame = await adb_manager.screencap()
    if frame is None:
        raise HTTPException(status_code=500, detail="Screencap failed")
    return {"png_base64": frame.hex()}


# --- ROI CRUD ---

class RoiCreateRequest(BaseModel):
    roi_name: str
    roi_type: str = "tap"
    x_pos: int
    y_pos: int
    width: int
    height: int


class RoiResponse(BaseModel):
    id: int
    roi_name: str
    roi_type: str
    x_pos: int
    y_pos: int
    width: int
    height: int
    image_path: str | None


@router.get("/roi")
async def list_rois():
    """List all saved ROI templates."""
    with get_session() as session:
        rois = session.query(RoiTemplate).all()
        return [
            RoiResponse(
                id=r.id,
                roi_name=r.roi_name,
                roi_type=r.roi_type,
                x_pos=r.x_pos,
                y_pos=r.y_pos,
                width=r.width,
                height=r.height,
                image_path=r.image_path,
            )
            for r in rois
        ]


@router.post("/roi")
async def create_roi(req: RoiCreateRequest) -> RoiResponse:
    """Create a new ROI template."""
    with get_session() as session:
        existing = session.query(RoiTemplate).filter_by(roi_name=req.roi_name).first()
        if existing:
            # Update existing
            existing.x_pos = req.x_pos
            existing.y_pos = req.y_pos
            existing.width = req.width
            existing.height = req.height
            existing.roi_type = req.roi_type
            session.commit()
            session.refresh(existing)
            return RoiResponse(
                id=existing.id,
                roi_name=existing.roi_name,
                roi_type=existing.roi_type,
                x_pos=existing.x_pos,
                y_pos=existing.y_pos,
                width=existing.width,
                height=existing.height,
                image_path=existing.image_path,
            )

        roi = RoiTemplate(
            roi_name=req.roi_name,
            roi_type=req.roi_type,
            x_pos=req.x_pos,
            y_pos=req.y_pos,
            width=req.width,
            height=req.height,
        )
        session.add(roi)
        session.commit()
        session.refresh(roi)
        return RoiResponse(
            id=roi.id,
            roi_name=roi.roi_name,
            roi_type=roi.roi_type,
            x_pos=roi.x_pos,
            y_pos=roi.y_pos,
            width=roi.width,
            height=roi.height,
            image_path=roi.image_path,
        )


@router.delete("/roi/{roi_id}")
async def delete_roi(roi_id: int):
    """Delete an ROI template."""
    with get_session() as session:
        roi = session.query(RoiTemplate).get(roi_id)
        if roi is None:
            raise HTTPException(status_code=404, detail="ROI not found")
        session.delete(roi)
        session.commit()
        return {"ok": True}


# --- OCR Preview ---

class OcrPreviewRequest(BaseModel):
    x: int
    y: int
    width: int
    height: int


@router.post("/ocr/preview")
async def ocr_preview(req: OcrPreviewRequest):
    """Crop the current screen to the given ROI and run OCR."""
    if not adb_manager.is_connected:
        raise HTTPException(status_code=503, detail="ADB not connected")

    frame = await adb_manager.screencap()
    if frame is None:
        raise HTTPException(status_code=500, detail="Screencap failed")

    try:
        import cv2, numpy as np
        from PIL import Image, ImageEnhance

        nparr = np.frombuffer(frame, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
        h, w = img.shape

        x1 = max(0, req.x)
        y1 = max(0, req.y)
        x2 = min(w, req.x + req.width)
        y2 = min(h, req.y + req.height)

        if x2 <= x1 or y2 <= y1:
            return {"text": "", "error": "Invalid ROI"}

        roi = img[y1:y2, x1:x2]
        cv2.imwrite("storage/templates/_ocr_crop.png", roi)

        text = ""
        # Try multiple preprocessing approaches
        approaches = []

        # Approach 1: Raw (no preprocessing)
        pil = Image.fromarray(roi)
        raw = pytesseract.image_to_string(pil, config="--psm 7 -c tessedit_char_whitelist=0123456789").strip()
        if raw: approaches.append(("raw", int(raw) if raw.isdigit() else None))

        # Approach 2: Scaled up 2x
        scaled = cv2.resize(roi, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        scaled_pil = Image.fromarray(scaled)
        s2 = pytesseract.image_to_string(scaled_pil, config="--psm 7 -c tessedit_char_whitelist=0123456789").strip()
        if s2: approaches.append(("scaled2x", int(s2) if s2.isdigit() else None))

        # Approach 3: Scaled 3x
        scaled3 = cv2.resize(roi, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        s3 = pytesseract.image_to_string(Image.fromarray(scaled3), config="--psm 7 -c tessedit_char_whitelist=0123456789").strip()
        if s3: approaches.append(("scaled3x", int(s3) if s3.isdigit() else None))

        # Approach 4: Inverted
        inv = cv2.bitwise_not(roi)
        inv_pil = Image.fromarray(inv)
        s4 = pytesseract.image_to_string(inv_pil, config="--psm 7 -c tessedit_char_whitelist=0123456789").strip()
        if s4: approaches.append(("inverted", int(s4) if s4.isdigit() else None))

        # Return most common numerical result
        nums = [v for _, v in approaches if v is not None]
        if nums:
            text = str(max(set(nums), key=nums.count))
        elif approaches:
            text = str(approaches[0][1] or approaches[0][0])

        details = [f"{name}={val}" for name, val in approaches]
        return {"text": text, "details": "; ".join(details), "crop": "saved to storage/templates/_ocr_crop.png"}

    except Exception as e:
        logger.error("OCR failed: %s", e)
        return {"text": "", "error": str(e)}


# --- Template Calibration ---

class TemplateCalibrateRequest(BaseModel):
    roi_name: str       # e.g., "gold_number"
    expected_value: str  # e.g., "1259145"


@router.post("/ocr/templates/calibrate")
async def calibrate_ocr_templates(req: TemplateCalibrateRequest):
    """Capture screen, extract digit templates from ROI using known value."""
    if not adb_manager.is_connected:
        raise HTTPException(status_code=503, detail="ADB not connected")

    with get_session() as session:
        roi = session.query(RoiTemplate).filter_by(roi_name=req.roi_name).first()
    if roi is None:
        raise HTTPException(status_code=404, detail=f"ROI '{req.roi_name}' not found")

    frame = await adb_manager.screencap()
    if frame is None:
        raise HTTPException(status_code=500, detail="Screencap failed")

    from backend.vision.ocr import calibrate_templates
    result = calibrate_templates(frame, roi.x_pos, roi.y_pos, roi.width, roi.height,
                                  req.roi_name, req.expected_value)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/ocr/templates")
async def list_ocr_templates():
    """List which ROIs have templates calibrated."""
    import os
    _TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "storage", "ocr_templates")
    if not os.path.isdir(_TEMPLATE_DIR):
        return {"templates": {}}
    result = {}
    for name in os.listdir(_TEMPLATE_DIR):
        d = os.path.join(_TEMPLATE_DIR, name)
        if os.path.isdir(d):
            files = sorted(os.listdir(d))
            result[name] = files
    return {"templates": result}