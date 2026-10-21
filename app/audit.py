"""Append-only audit log. Callers never update or delete these rows."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import AuditLog


def audit(
    session: Session,
    action: str,
    entity: str,
    entity_id: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
    actor: str = "owner",
) -> None:
    session.add(
        AuditLog(
            actor=actor,
            action=action,
            entity=entity,
            entity_id=entity_id,
            before=before,
            after=after,
        )
    )
