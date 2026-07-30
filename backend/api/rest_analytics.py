"""REST endpoints for analytics and metrics."""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter
from sqlmodel import Session, select, func

from backend.db.database import engine
from backend.db.models import AttackLog

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/analytics")


@router.get("/loot-rate")
async def loot_rate(hours: int = 24):
    """Get loot per hour for Chart.js line chart."""
    with Session(engine) as session:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        stmt = (
            select(
                func.strftime("%Y-%m-%dT%H:00:00", AttackLog.timestamp).label("hour"),
                func.sum(AttackLog.gold_earned).label("gold"),
                func.sum(AttackLog.elixir_earned).label("elixir"),
                func.sum(AttackLog.dark_elixir_earned).label("dark_elixir"),
                func.count(AttackLog.id).label("attacks"),
            )
            .where(AttackLog.timestamp >= cutoff)
            .group_by("hour")
            .order_by("hour")
        )
        result = session.exec(stmt).all()
        return [
            {
                "hour": r[0],
                "gold": r[1],
                "elixir": r[2],
                "dark_elixir": r[3],
                "attacks": r[4],
            }
            for r in result
        ]


@router.get("/search-efficiency")
async def search_efficiency():
    """Get average/max/min search counts."""
    with Session(engine) as session:
        stmt = select(
            func.avg(AttackLog.search_count).label("avg"),
            func.max(AttackLog.search_count).label("max"),
            func.min(AttackLog.search_count).label("min"),
            func.count(AttackLog.id).label("total"),
        )
        result = session.exec(stmt).one()
        return {
            "avg_skips": round(result[0] or 0, 1),
            "max_skips": result[1] or 0,
            "min_skips": result[2] or 0,
            "total_attacks": result[3],
        }


@router.get("/history")
async def attack_history(limit: int = 50):
    """Get recent attack logs."""
    with Session(engine) as session:
        stmt = select(AttackLog).order_by(AttackLog.timestamp.desc()).limit(limit)
        result = session.exec(stmt).all()
        return [
            {
                "id": r.id,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "gold_earned": r.gold_earned,
                "elixir_earned": r.elixir_earned,
                "dark_elixir_earned": r.dark_elixir_earned,
                "trophies_change": r.trophies_change,
                "search_count": r.search_count,
            }
            for r in result
        ]


@router.get("/summary")
async def summary():
    """Get overall summary statistics."""
    with Session(engine) as session:
        stmt = select(
            func.coalesce(func.sum(AttackLog.gold_earned), 0).label("total_gold"),
            func.coalesce(func.sum(AttackLog.elixir_earned), 0).label("total_elixir"),
            func.coalesce(func.sum(AttackLog.dark_elixir_earned), 0).label("total_de"),
            func.count(AttackLog.id).label("total_raids"),
        )
        result = session.exec(stmt).one()
        return {
            "total_gold": result[0],
            "total_elixir": result[1],
            "total_dark_elixir": result[2],
            "total_raids": result[3],
        }
