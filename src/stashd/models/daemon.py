"""What the controller knows about a daemon: only what the daemon told it.

Which storages a daemon *may* serve is in the cluster config; this is liveness, not
topology.
"""

from datetime import datetime

from sqlalchemy import ARRAY, String
from sqlalchemy.orm import Mapped, mapped_column

from stashd.models.base import Base, Count, Name


class Daemon(Base):
    __tablename__ = "daemons"

    id: Mapped[Name] = mapped_column(primary_key=True)
    storages: Mapped[list[str]] = mapped_column(ARRAY(String(255)))
    config_revision: Mapped[Count]
    version: Mapped[Name]
    last_seen_at: Mapped[datetime]
