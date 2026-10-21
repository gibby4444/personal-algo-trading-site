"""Engine, sessions, and startup migrations. Postgres when DATABASE_URL is set, else SQLite."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Base, missing_column_names

_engine = None
_Session: sessionmaker[Session] | None = None


def reset_db() -> None:
    """Drop the cached engine. Tests use this between databases."""
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _Session = None


def get_engine():
    global _engine, _Session
    if _engine is None:
        settings = get_settings()
        url = settings.database_url
        if url.startswith("sqlite:///"):
            path = url[len("sqlite:///") :]
            if path and path != ":memory:" and not path.startswith("file:"):
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True, future=True)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    get_engine()
    assert _Session is not None
    return _Session


@contextmanager
def session_scope() -> Iterator[Session]:
    db = get_sessionmaker()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def migrate() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    insp_pairs = missing_column_names(engine)
    if not insp_pairs:
        return
    dialect = engine.dialect
    tables = {table.name: table for table in Base.metadata.sorted_tables}
    with engine.begin() as conn:
        for table_name, column_name in insp_pairs:
            column = tables[table_name].columns[column_name]
            ddl = column.type.compile(dialect=dialect)
            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"))


def init_db() -> None:
    from app.seed import seed

    migrate()
    with session_scope() as db:
        seed(db)


def ping() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
