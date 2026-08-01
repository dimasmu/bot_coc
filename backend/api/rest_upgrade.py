"""REST endpoints for upgrade queue management."""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db.database import get_session
from backend.db.models import UpgradeQueue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/upgrade")

# --- Request/Response Models ---

class UpgradeItemCreate(BaseModel):
    name: str
    target_level: int
    resource_type: str = "gold"
    upgrade_type: str = "building"
    priority_order: int | None = None

class UpgradeItemUpdate(BaseModel):
    name: str | None = None
    target_level: int | None = None
    resource_type: str | None = None
    priority_order: int | None = None
    status: str | None = None
    cost: int | None = None

class UpgradeItemResponse(BaseModel):
    id: int
    name: str
    target_level: int
    resource_type: str
    upgrade_type: str
    cost: int | None
    priority_order: int
    status: str
    started_at: datetime | None
    completed_at: datetime | None

# --- Endpoints ---

@router.get("/queue")
async def list_queue():
    """List all queue items ordered by priority."""
    with get_session() as session:
        items = session.query(UpgradeQueue).order_by(UpgradeQueue.priority_order).all()
        return [UpgradeItemResponse(
            id=i.id, name=i.name, target_level=i.target_level,
            resource_type=i.resource_type, upgrade_type=i.upgrade_type,
            cost=i.cost, priority_order=i.priority_order,
            status=i.status, started_at=i.started_at, completed_at=i.completed_at,
        ) for i in items]


@router.post("/queue")
async def create_item(req: UpgradeItemCreate):
    """Add a new item to the upgrade queue."""
    with get_session() as session:
        if req.priority_order is None:
            highest = session.query(UpgradeQueue).order_by(
                UpgradeQueue.priority_order.desc()).first()
            priority = (highest.priority_order + 1) if highest else 1
        else:
            priority = req.priority_order

        item = UpgradeQueue(
            name=req.name, target_level=req.target_level,
            resource_type=req.resource_type, upgrade_type=req.upgrade_type,
            priority_order=priority,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return UpgradeItemResponse(
            id=item.id, name=item.name, target_level=item.target_level,
            resource_type=item.resource_type, upgrade_type=item.upgrade_type,
            cost=item.cost, priority_order=item.priority_order,
            status=item.status, started_at=item.started_at, completed_at=item.completed_at,
        )


@router.put("/queue/{item_id}")
async def update_item(item_id: int, req: UpgradeItemUpdate):
    """Update an upgrade queue item."""
    with get_session() as session:
        item = session.query(UpgradeQueue).get(item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        if req.name is not None:
            item.name = req.name
        if req.target_level is not None:
            item.target_level = req.target_level
        if req.resource_type is not None:
            item.resource_type = req.resource_type
        if req.priority_order is not None:
            item.priority_order = req.priority_order
        if req.status is not None:
            item.status = req.status
            if req.status == "IN_PROGRESS":
                item.started_at = datetime.utcnow()
            elif req.status == "COMPLETED":
                item.completed_at = datetime.utcnow()
        if req.cost is not None:
            item.cost = req.cost
        session.commit()
        return {"ok": True}


@router.delete("/queue/{item_id}")
async def delete_item(item_id: int):
    """Remove an item from the upgrade queue."""
    with get_session() as session:
        item = session.query(UpgradeQueue).get(item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        session.delete(item)
        session.commit()
        return {"ok": True}


@router.patch("/queue/{item_id}/status")
async def update_item_status(item_id: int, status: str = "PENDING", cost: int | None = None):
    """Update an item's status and optionally its cost."""
    with get_session() as session:
        item = session.query(UpgradeQueue).get(item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        item.status = status
        if status == "IN_PROGRESS":
            item.started_at = datetime.utcnow()
        elif status == "COMPLETED":
            item.completed_at = datetime.utcnow()
        if cost is not None:
            item.cost = cost
        session.commit()
        return {"ok": True}


@router.get("/status")
async def upgrade_status():
    """Current upgrade queue status + pending count."""
    with get_session() as session:
        pending = session.query(UpgradeQueue).filter_by(status="PENDING").count()
        in_progress = session.query(UpgradeQueue).filter_by(status="IN_PROGRESS").count()
        completed = session.query(UpgradeQueue).filter_by(status="COMPLETED").count()
        return {
            "pending": pending,
            "in_progress": in_progress,
            "completed": completed,
            "total": pending + in_progress + completed,
        }
