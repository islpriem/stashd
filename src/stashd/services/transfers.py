"""Reading the queue and the transfer history."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.domain.errors import NotFound
from stashd.domain.transfers import TransferKind, TransferState
from stashd.models import Transfer

MAX_PAGE = 500


async def list_transfers(
    session: AsyncSession,
    *,
    user: str | None = None,
    state: TransferState | None = None,
    kind: TransferKind | None = None,
    storage: str | None = None,
    route: str | None = None,
    fileset_id: int | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[Sequence[Transfer], str | None]:
    """Newest first, cursor-paginated on the transfer id."""
    page = min(limit, MAX_PAGE)
    query = sa.select(Transfer).order_by(Transfer.id.desc()).limit(page + 1)
    if user is not None:
        query = query.where(Transfer.user == user)
    if state is not None:
        query = query.where(Transfer.state == state)
    if kind is not None:
        query = query.where(Transfer.kind == kind)
    if route is not None:
        query = query.where(Transfer.route == route)
    if fileset_id is not None:
        query = query.where(Transfer.fileset_id == fileset_id)
    if storage is not None:
        query = query.where(
            sa.or_(
                Transfer.route.startswith(f"{storage}->"),
                Transfer.route.endswith(f"->{storage}"),
            )
        )
    if cursor is not None:
        query = query.where(Transfer.id < int(cursor))

    rows = list(await session.scalars(query))
    if len(rows) > page:
        return rows[:page], str(rows[page - 1].id)
    return rows, None


async def get_transfer(session: AsyncSession, transfer_id: int) -> Transfer:
    transfer = await session.get(Transfer, transfer_id)
    if transfer is None:
        raise NotFound(f"no transfer with id {transfer_id}", transfer_id=transfer_id)
    return transfer
