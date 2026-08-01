"""SQLite database engine and session management."""

from sqlmodel import create_engine, SQLModel, Session
from sqlalchemy import text
from backend.config import settings

engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
    echo=False,
)


def init_db():
    """Create all tables if they don't exist."""
    # Add roi_type column if it doesn't exist (migration for existing DB)
    try:
        with Session(engine) as session:
            session.exec(text("ALTER TABLE roi_templates ADD COLUMN roi_type VARCHAR(10) DEFAULT 'tap'"))
            session.commit()
    except Exception:
        pass  # Column already exists

    SQLModel.metadata.create_all(engine)

    from backend.db.models import Config

    # Seed default config values if they don't exist
    with Session(engine) as session:
        defaults = [
            ("min_gold_threshold", "300000", "FARMING"),
            ("min_elixir_threshold", "300000", "FARMING"),
            ("min_dark_elixir_threshold", "500", "FARMING"),
            ("max_searches", "30", "FARMING"),
            ("strategy", "4finger", "FARMING"),
        ]
        for key, value, category in defaults:
            if not session.query(Config).filter_by(key=key).first():
                session.add(Config(key=key, value=value, category=category))
        session.commit()

    # Seed default ROI presets for 1280x720
    from backend.db.models import RoiTemplate
    from sqlmodel import select
    roi_defaults = [
        ("btn_attack", "tap", 5, 560, 60, 130),
        ("btn_find_match", "tap", 569, 444, 286, 93),
        ("btn_next", "tap", 1069, 437, 203, 102),
        ("gold_number", "read", 22, 93, 150, 43),
        ("elixir_number", "read", 22, 128, 150, 45),
        ("de_number", "read", 22, 164, 150, 43),
        ("btn_return_home", "tap", 50, 580, 160, 100),
        ("btn_surrender", "tap", 80, 660, 100, 50),
    ]
    with Session(engine) as session:
        for name, rtype, x, y, w, h in roi_defaults:
            existing = session.exec(select(RoiTemplate).where(RoiTemplate.roi_name == name)).first()
            if not existing:
                session.add(RoiTemplate(roi_name=name, roi_type=rtype, x_pos=x, y_pos=y, width=w, height=h))
        session.commit()

    # Seed default Attack Loop sequence
    from backend.db.models import BotSequence, SequenceStep

    with Session(engine) as session:
        existing = session.exec(select(BotSequence).where(BotSequence.name == "Attack Loop")).first()
        if not existing:
            seq = BotSequence(name="Attack Loop", description="Full attack cycle", is_active=True)
            session.add(seq)
            session.commit()
            session.refresh(seq)
            steps = [
                ("tap", 0, "btn_attack", None, None),
                ("wait", 1, None, 3.0, None),
                ("tap", 2, "btn_find_match", None, None),
                ("wait", 3, None, 6.0, None),
                ("tap", 4, "myarmy_btn_attack", None, None),
                ("wait", 5, None, 3.0, None),
                ("search", 6, None, None, '{"max_searches":30}'),
                ("attack", 7, None, None, '{"strategy":"4finger","duration":180}'),
                ("return_home", 8, None, None, None),
                ("upgrade_check", 9, None, None, None),
                ("upgrade_execute", 10, None, None, None),
            ]
            for stype, order, roi, dur, cfg in steps:
                session.add(SequenceStep(
                    sequence_id=seq.id,
                    step_order=order,
                    step_type=stype,
                    roi_name=roi,
                    duration=dur,
                    config_json=cfg,
                ))
            session.commit()

        # Ensure upgrade steps exist on the default Attack Loop sequence (handles pre-existing DBs)
        seq = session.exec(select(BotSequence).where(BotSequence.name == "Attack Loop")).first()
        if seq:
            for order, stype in ((9, "upgrade_check"), (10, "upgrade_execute")):
                exists = session.exec(
                    select(SequenceStep).where(
                        SequenceStep.sequence_id == seq.id,
                        SequenceStep.step_order == order,
                    )
                ).first()
                if not exists:
                    session.add(SequenceStep(sequence_id=seq.id, step_order=order, step_type=stype))
            session.commit()


def get_session():
    """Get a new SQLModel session."""
    return Session(engine)
