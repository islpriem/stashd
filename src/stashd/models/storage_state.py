"""Runtime state of a storage system. Its topology is in the cluster config."""

from sqlalchemy.orm import Mapped, mapped_column

from stashd.models.base import Base, Name


class StorageState(Base):
    __tablename__ = "storage_states"

    storage_id: Mapped[Name] = mapped_column(primary_key=True)
    drained: Mapped[bool] = mapped_column(default=False)
