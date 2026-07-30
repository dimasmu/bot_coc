"""REST endpoints for bot configuration."""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db.database import get_session
from backend.db.models import Config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/config")


class ConfigItem(BaseModel):
    key: str
    value: str
    category: str


class ConfigResponse(BaseModel):
    key: str
    value: str
    category: str
    updated_at: str | None


@router.get("")
async def list_config(category: str | None = None):
    """List all config items, optionally filtered by category."""
    with get_session() as session:
        q = session.query(Config)
        if category:
            q = q.filter_by(category=category)
        items = q.all()
        return [
            ConfigResponse(
                key=c.key,
                value=c.value,
                category=c.category,
                updated_at=c.updated_at.isoformat() if c.updated_at else None,
            )
            for c in items
        ]


@router.put("/{key}")
async def set_config(key: str, item: ConfigItem):
    """Set or update a config value."""
    with get_session() as session:
        existing = session.query(Config).filter_by(key=key).first()
        if existing:
            existing.value = item.value
            existing.category = item.category
            existing.updated_at = datetime.utcnow()
        else:
            session.add(Config(key=key, value=item.value, category=item.category))
        session.commit()

        updated = session.query(Config).filter_by(key=key).first()
        return ConfigResponse(
            key=updated.key,
            value=updated.value,
            category=updated.category,
            updated_at=updated.updated_at.isoformat() if updated.updated_at else None,
        )


@router.delete("/{key}")
async def delete_config(key: str):
    """Delete a config key."""
    with get_session() as session:
        item = session.query(Config).filter_by(key=key).first()
        if not item:
            raise HTTPException(status_code=404, detail="Config key not found")
        session.delete(item)
        session.commit()
        return {"ok": True}
