"""Reading filesets. Every fileset is readable by everyone."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.domain.errors import NotFound
from stashd.domain.filesets import FilesetKind, FilesetState
from stashd.models import Fileset


async def list_filesets(
    session: AsyncSession,
    *,
    storage: str | None = None,
    user: str | None = None,
    kind: FilesetKind | None = None,
    state: FilesetState | None = None,
) -> Sequence[Fileset]:
    query = sa.select(Fileset).order_by(Fileset.storage_id, Fileset.owner_user, Fileset.name)
    if storage is not None:
        query = query.where(Fileset.storage_id == storage)
    if user is not None:
        query = query.where(Fileset.owner_user == user)
    if kind is not None:
        query = query.where(Fileset.kind == kind)
    if state is not None:
        query = query.where(Fileset.state == state)
    return list(await session.scalars(query))


async def get_fileset(session: AsyncSession, fileset_id: int) -> Fileset:
    fileset = await session.get(Fileset, fileset_id)
    if fileset is None:
        raise NotFound(f"no fileset with id {fileset_id}", fileset_id=fileset_id)
    return fileset
