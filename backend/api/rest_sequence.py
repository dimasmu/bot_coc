"""REST endpoints for bot sequences and steps."""

import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db.database import get_session
from backend.db.models import BotSequence, SequenceStep

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/sequences")


class StepResponse(BaseModel):
    id: int
    step_order: int
    step_type: str
    roi_name: str | None = None
    duration: float | None = None
    config_json: str | None = None


class SequenceResponse(BaseModel):
    id: int
    name: str
    description: str
    is_active: bool
    steps: list[StepResponse]


class CreateSequenceRequest(BaseModel):
    name: str
    description: str = ""


class StepUpdateRequest(BaseModel):
    step_order: int | None = None
    step_type: str | None = None
    roi_name: str | None = None
    duration: float | None = None
    config_json: str | None = None


@router.post("")
async def create_sequence(req: CreateSequenceRequest):
    """Create a new bot sequence."""
    with get_session() as session:
        existing = session.query(BotSequence).filter_by(name=req.name).first()
        if existing:
            raise HTTPException(status_code=409, detail="Sequence name already exists")
        seq = BotSequence(name=req.name, description=req.description)
        session.add(seq)
        session.commit()
        session.refresh(seq)
        return {"id": seq.id, "name": seq.name, "description": seq.description, "is_active": seq.is_active}


@router.get("")
async def list_sequences():
    with get_session() as session:
        seqs = session.query(BotSequence).all()
        result = []
        for seq in seqs:
            steps = session.query(SequenceStep).filter_by(sequence_id=seq.id).order_by(SequenceStep.step_order).all()
            result.append(SequenceResponse(
                id=seq.id,
                name=seq.name,
                description=seq.description,
                is_active=seq.is_active,
                steps=[StepResponse(id=s.id, step_order=s.step_order, step_type=s.step_type, roi_name=s.roi_name, duration=s.duration, config_json=s.config_json) for s in steps],
            ))
        return result


@router.put("/{seq_id}/activate")
async def activate_sequence(seq_id: int):
    with get_session() as session:
        # Deactivate all
        for s in session.query(BotSequence).all():
            s.is_active = False
        # Activate selected
        seq = session.query(BotSequence).get(seq_id)
        if not seq:
            raise HTTPException(status_code=404, detail="Sequence not found")
        seq.is_active = True
        session.commit()
        return {"ok": True}


@router.put("/{seq_id}/steps")
async def update_steps(seq_id: int, steps: list[StepUpdateRequest]):
    with get_session() as session:
        seq = session.query(BotSequence).get(seq_id)
        if not seq:
            raise HTTPException(status_code=404, detail="Sequence not found")
        # Delete existing steps
        session.query(SequenceStep).filter_by(sequence_id=seq_id).delete()
        # Re-add in new order
        for i, step in enumerate(steps):
            session.add(SequenceStep(
                sequence_id=seq_id,
                step_order=i,
                step_type=step.step_type,
                roi_name=step.roi_name,
                duration=step.duration,
                config_json=step.config_json,
            ))
        session.commit()
        return {"ok": True}


@router.delete("/{seq_id}")
async def delete_sequence(seq_id: int):
    with get_session() as session:
        seq = session.query(BotSequence).get(seq_id)
        if not seq:
            raise HTTPException(status_code=404)
        # Delete steps first
        session.query(SequenceStep).filter_by(sequence_id=seq_id).delete()
        session.delete(seq)
        session.commit()
        return {"ok": True}


@router.get("/active")
async def get_active_sequence():
    with get_session() as session:
        seq = session.query(BotSequence).filter_by(is_active=True).first()
        if not seq:
            return {"steps": []}
        steps = session.query(SequenceStep).filter_by(sequence_id=seq.id).order_by(SequenceStep.step_order).all()
        return {
            "id": seq.id,
            "name": seq.name,
            "steps": [{"step_order": s.step_order, "step_type": s.step_type, "roi_name": s.roi_name, "duration": s.duration, "config_json": s.config_json} for s in steps],
        }
