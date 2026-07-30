"""SQLModel database models matching DRD schema."""

from datetime import datetime
from sqlmodel import SQLModel, Field


class Config(SQLModel, table=True):
    __tablename__ = "configs"
    key: str = Field(primary_key=True, max_length=64)
    value: str
    category: str = Field(max_length=32)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RoiTemplate(SQLModel, table=True):
    __tablename__ = "roi_templates"
    id: int = Field(default=None, primary_key=True)
    roi_name: str = Field(max_length=64, unique=True, index=True)
    x_pos: int
    y_pos: int
    width: int
    height: int
    roi_type: str = Field(default="tap", max_length=10)
    image_path: str | None = Field(default=None, max_length=255)


class AttackLog(SQLModel, table=True):
    __tablename__ = "attack_logs"
    id: int = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)
    gold_earned: int = 0
    elixir_earned: int = 0
    dark_elixir_earned: int = 0
    trophies_change: int = 0
    search_count: int = 1


class UpgradeQueue(SQLModel, table=True):
    __tablename__ = "upgrade_queue"
    id: int = Field(default=None, primary_key=True)
    building_name: str = Field(max_length=64)
    target_level: int
    priority_order: int
    status: str = Field(default="PENDING", max_length=20)
    started_at: datetime | None = None
    completed_at: datetime | None = None
