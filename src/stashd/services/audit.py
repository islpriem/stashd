"""Every mutating request leaves a row behind."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from stashd.domain.clock import Clock
from stashd.domain.identity import Principal
from stashd.models import AuditEvent

OK = "ok"


def record(
    session: AsyncSession,
    clock: Clock,
    *,
    actor: Principal,
    subject_user: str,
    object_type: str,
    action: str,
    result: str = OK,
    object_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Adds the event to the session; the caller commits it with its own change."""
    session.add(
        AuditEvent(
            ts=clock.now(),
            actor_uid=actor.uid,
            subject_user=subject_user,
            object_type=object_type,
            object_id=object_id,
            action=action,
            result=result,
            detail=detail,
        )
    )
