"""Stored fair-share points; decay is applied lazily on read."""

from datetime import datetime

from sqlalchemy import Float
from sqlalchemy.orm import Mapped, mapped_column

from stashd.models.base import Base, Name


class FairShareAccountRow(Base):
    __tablename__ = "fairshare_accounts"

    user: Mapped[Name] = mapped_column(primary_key=True)
    points: Mapped[float] = mapped_column(Float, default=0.0)
    decayed_at: Mapped[datetime]
