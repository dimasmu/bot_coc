"""Tests for the Farming Loop DB migration (V4: restore attack steps)."""

from sqlmodel import Session, SQLModel, create_engine

import pytest

import backend.db.database as db_mod
from backend.db.models import BotSequence, Config, SequenceStep

ATTACK_STEPS = [
    ("tap", "btn_attack"),
    ("wait", None),
    ("tap", "btn_find_match"),
    ("wait", None),
    ("tap", "myarmy_btn_attack"),
    ("wait", None),
    ("search", None),
    ("attack", None),
    ("return_home", None),
]


@pytest.fixture
def temp_engine(tmp_path, monkeypatch):
    """Point the db module at a fresh temp SQLite DB for this test."""
    db_path = tmp_path / "test_coc.db"
    monkeypatch.setattr(db_mod.settings, "db_path", str(db_path))
    engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(db_mod, "engine", engine)
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_v3_idle_farming(engine):
    """Create a v3-style DB: Farming Loop with a single idle wait step."""
    with Session(engine) as s:
        seq = BotSequence(name="Farming Loop",
                          description="Full attack farming cycle",
                          is_active=True)
        s.add(seq)
        s.commit()
        s.refresh(seq)
        s.add(SequenceStep(sequence_id=seq.id, step_order=0,
                           step_type="wait", duration=1.0))
        s.add(Config(key="db_version", value="3", category="SYSTEM"))
        s.commit()


def test_migration_v4_restores_attack_steps(temp_engine):
    _seed_v3_idle_farming(temp_engine)

    db_mod.init_db()

    with Session(temp_engine) as s:
        seq = s.query(BotSequence).filter_by(name="Farming Loop").first()
        steps = s.query(SequenceStep).where(
            SequenceStep.sequence_id == seq.id
        ).order_by(SequenceStep.step_order).all()
        assert [(st.step_type, st.roi_name) for st in steps] == ATTACK_STEPS

        ver = s.query(Config).filter_by(key="db_version").first()
        assert ver.value == "4"


def test_fresh_db_gets_attack_steps(temp_engine):
    """A brand-new DB (no version row) must also seed the attack steps."""
    db_mod.init_db()

    with Session(temp_engine) as s:
        seq = s.query(BotSequence).filter_by(name="Farming Loop").first()
        assert seq is not None
        steps = s.query(SequenceStep).where(
            SequenceStep.sequence_id == seq.id
        ).order_by(SequenceStep.step_order).all()
        assert [(st.step_type, st.roi_name) for st in steps] == ATTACK_STEPS
