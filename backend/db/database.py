"""SQLite database engine and session management."""

from sqlmodel import create_engine, SQLModel, Session
from backend.config import settings

engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
    echo=False,
)


def init_db():
    """Create all tables if they don't exist."""
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


def get_session():
    """Get a new SQLModel session."""
    return Session(engine)
