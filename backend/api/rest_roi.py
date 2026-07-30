"""REST endpoints for ROI template management and screenshot capture."""

import io
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
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
        import cv2
        import numpy as np
        from PIL import Image
        from backend.vision.ocr import read_number
        import pytesseract

        nparr = np.frombuffer(frame, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
        h, w = img.shape

        x1 = max(0, req.x)
        y1 = max(0, req.y)
        x2 = min(w, req.x + req.width)
        y2 = min(h, req.y + req.height)

        if x2 <= x1 or y2 <= y1:
            return {"text": "", "error": "Invalid ROI dimensions"}

        roi = img[y1:y2, x1:x2]

        # Save crop for debugging
        cv2.imwrite("storage/templates/_ocr_crop.png", roi)

        # Strategy: try Tesseract with multiple configs, return best
        results = []
        for scale in [1, 2, 3]:
            if scale > 1:
                proc = cv2.resize(roi, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            else:
                proc = roi

            pil_img = Image.fromarray(proc)
            text = pytesseract.image_to_string(
                pil_img, config="--psm 7 -c tessedit_char_whitelist=0123456789"
            ).strip()
            if text:
                results.append(int(text))

        if results:
            return {"text": str(max(set(results), key=results.count))}
        else:
            return {"text": "", "error": "No digits found. Crop saved to storage/templates/_ocr_crop.png"}

    except Exception as e:
        logger.error("OCR failed: %s", e)
        return {"text": "", "error": str(e)}
