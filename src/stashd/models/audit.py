"""The audit trail: one row per mutating request."""

from datetime import datetime
from typing import Any

from sqlalchemy import Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from stashd.models.base import Base, Count, Name


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[datetime]
    actor_uid: Mapped[Count]
    subject_user: Mapped[Name]
    object_type: Mapped[Name]
    object_id: Mapped[Name | None] = mapped_column(default=None)
    action: Mapped[Name] = mapped_column()
    result: Mapped[Name] = mapped_column()
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)

    __table_args__ = (Index("ix_audit_events_ts", "ts"),)
