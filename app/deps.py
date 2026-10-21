"""Database dependency shared by the pages and the JSON API."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.db import get_sessionmaker


def get_db() -> Iterator[Session]:
    db = get_sessionmaker()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
