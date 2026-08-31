"""Schema constraints that the code above must be able to rely on."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.domain.filesets import FilesetKind, FilesetState
from stashd.domain.transfers import TransferKind, TransferState
from stashd.models import Fileset, Transfer

pytestmark = pytest.mark.integration

GIB = 1024**3
T0 = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def fileset(**overrides: object) -> Fileset:
    values: dict[str, object] = {
        "name": "mydir",
        "owner_user": "mmustermann",
        "owner_uid": 1000,
        "owner_gid": 1000,
        "storage_id": "LOC2HOT",
        "kind": FilesetKind.CACHED,
        "state": FilesetState.READY,
        "path": "/cache/loc2/mmustermann/mydir",
        "allocated_bytes": 21 * GIB,
        "created_at": T0,
    }
    return Fileset(**{**values, **overrides})


def transfer(fileset_id: int, **overrides: object) -> Transfer:
    values: dict[str, object] = {
        "kind": TransferKind.WARM,
        "user": "mmustermann",
        "fileset_id": fileset_id,
        "state": TransferState.SUBMITTED,
        "route": "HOT1->LOC2HOT",
        "submitted_at": T0,
    }
    return Transfer(**{**values, **overrides})


async def stored(session: AsyncSession, *rows: object) -> None:
    session.add_all(rows)
    await session.commit()


class TestFilesetConstraints:
    async def test_a_second_live_fileset_with_the_same_name_is_refused(
        self, session: AsyncSession
    ) -> None:
        await stored(session, fileset())

        with pytest.raises(IntegrityError):
            await stored(session, fileset(state=FilesetState.POPULATING))

    async def test_a_released_name_becomes_reusable(self, session: AsyncSession) -> None:
        await stored(session, fileset(state=FilesetState.RELEASED, released_at=T0))

        await stored(session, fileset())

        count = await session.scalar(sa.select(sa.func.count()).select_from(Fileset))
        assert count == 2

    async def test_two_released_filesets_may_share_a_name(self, session: AsyncSession) -> None:
        await stored(session, fileset(state=FilesetState.RELEASED))
        await stored(session, fileset(state=FilesetState.RELEASED))

    async def test_the_same_name_on_another_storage_is_allowed(
        self, session: AsyncSession
    ) -> None:
        await stored(session, fileset(), fileset(storage_id="OTHER"))

    async def test_a_zero_allocation_is_refused(self, session: AsyncSession) -> None:
        with pytest.raises(IntegrityError):
            await stored(session, fileset(allocated_bytes=0))

    async def test_an_unknown_state_is_refused_by_the_database_itself(
        self, session: AsyncSession
    ) -> None:
        """The enum is a check constraint in PostgreSQL, not only a Python guard."""
        insert = sa.text(
            "INSERT INTO filesets (name, owner_user, owner_uid, owner_gid, storage_id, kind,"
            " state, path, allocated_bytes, used_bytes, over_allocation, created_at)"
            " VALUES ('mydir', 'mmustermann', 1000, 1000, 'LOC2HOT', 'cached', 'ALMOST_READY',"
            " '/p', 1, 0, false, now())"
        )

        with pytest.raises(IntegrityError):
            await session.execute(insert)

    async def test_the_history_fields_round_trip(self, session: AsyncSession) -> None:
        await stored(
            session,
            fileset(
                source_storage_id="HOT1",
                source_path="/myuser/mydirectory",
                warm_started_at=T0,
                warm_finished_at=T0 + timedelta(hours=2),
                last_flushed_at=T0 + timedelta(hours=3),
                last_flush_target="PROJECT:/myuser/results",
                used_bytes=20 * GIB,
                used_bytes_at=T0 + timedelta(hours=4),
                file_count=12043,
                last_transfer_id=1,
            ),
        )
        session.expire_all()

        stored_fileset = (await session.scalars(sa.select(Fileset))).one()
        assert stored_fileset.source_path == "/myuser/mydirectory"
        assert stored_fileset.warm_finished_at == T0 + timedelta(hours=2)
        assert stored_fileset.warm_finished_at is not None
        assert stored_fileset.warm_finished_at.tzinfo is not None
        assert stored_fileset.last_flush_target == "PROJECT:/myuser/results"
        assert stored_fileset.file_count == 12043

    async def test_a_long_path_is_stored_whole(self, session: AsyncSession) -> None:
        long_path = "/cache/loc2/mmustermann/" + "d" * 600

        await stored(session, fileset(path=long_path))

        assert (await session.scalars(sa.select(Fileset))).one().path == long_path


class TestTransferConstraints:
    async def test_ids_come_from_a_sequence_and_increase(self, session: AsyncSession) -> None:
        await stored(session, fileset())
        owner = (await session.scalars(sa.select(Fileset))).one()

        await stored(session, transfer(owner.id))
        await stored(session, transfer(owner.id))

        ids = list(await session.scalars(sa.select(Transfer.id).order_by(Transfer.id)))
        assert ids == [1, 2]

    async def test_progress_may_not_exceed_the_total(self, session: AsyncSession) -> None:
        await stored(session, fileset())
        owner = (await session.scalars(sa.select(Fileset))).one()

        with pytest.raises(IntegrityError):
            await stored(session, transfer(owner.id, bytes_total=10, bytes_done=11))

    async def test_a_transfer_needs_a_fileset(self, session: AsyncSession) -> None:
        with pytest.raises(IntegrityError):
            await stored(session, transfer(4711))

    async def test_the_kind_is_stored_as_its_wire_value(self, session: AsyncSession) -> None:
        await stored(session, fileset())
        owner = (await session.scalars(sa.select(Fileset))).one()
        await stored(session, transfer(owner.id, kind=TransferKind.FLUSH))

        raw = await session.scalar(sa.text("SELECT kind FROM transfers LIMIT 1"))
        assert raw == "flush"
