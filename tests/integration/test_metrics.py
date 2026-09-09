"""The Prometheus endpoint on the controller."""

from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.domain.filesets import FilesetKind, FilesetState
from stashd.domain.transfers import TransferKind, TransferState
from stashd.models import Daemon, Fileset, Transfer

pytestmark = pytest.mark.integration

GIB = 1024**3
T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
# Served at the root, not under the versioned API.
METRICS = "http://controller/metrics"


def sample(text: str, name: str) -> dict[str, float]:
    """Every sample of one metric, keyed by its label string."""
    found = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.startswith(name):
            continue
        head, _, value = line.rpartition(" ")
        if head == name or head.startswith(f"{name}{{"):
            found[head[len(name) :]] = float(value)
    return found


async def seed(session: AsyncSession) -> None:
    fileset = Fileset(
        name="mydir",
        owner_user="mmustermann",
        owner_uid=1000,
        owner_gid=1000,
        storage_id="LOC2HOT",
        kind=FilesetKind.CACHED,
        state=FilesetState.READY,
        path="/fake/cache/mmustermann/mydir",
        allocated_bytes=20 * GIB,
        used_bytes=15 * GIB,
        created_at=T0,
    )
    session.add(fileset)
    await session.flush()
    session.add(
        Transfer(
            kind=TransferKind.WARM,
            user="mmustermann",
            fileset_id=fileset.id,
            state=TransferState.SUCCEEDED,
            route="HOT1->LOC2HOT",
            bytes_total=10 * GIB,
            bytes_done=10 * GIB,
            submitted_at=T0,
            started_at=T0 + timedelta(minutes=2),
            finished_at=T0 + timedelta(minutes=12),
        )
    )
    session.add(
        Transfer(
            kind=TransferKind.WARM,
            user="jdoe",
            fileset_id=fileset.id,
            state=TransferState.SUBMITTED,
            route="HOT1->LOC2HOT",
            bytes_total=5 * GIB,
            submitted_at=T0,
        )
    )
    await session.commit()


class TestMetrics:
    async def test_it_is_served_without_a_credential(self, api: httpx2.AsyncClient) -> None:
        response = await api.get(METRICS, headers={"Authorization": ""})

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]

    async def test_transfers_are_counted_by_kind_state_and_route(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await seed(session)

        text = (await api.get(METRICS)).text

        counted = sample(text, "stash_transfers_total")
        assert counted['{kind="warm",route="HOT1->LOC2HOT",state="SUCCEEDED"}'] == 1.0
        assert counted['{kind="warm",route="HOT1->LOC2HOT",state="SUBMITTED"}'] == 1.0

    async def test_bytes_moved_are_counted_by_route(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await seed(session)

        text = (await api.get(METRICS)).text

        assert sample(text, "stash_transfer_bytes_total") == {
            '{route="HOT1->LOC2HOT"}': float(10 * GIB)
        }

    async def test_duration_and_queue_wait_are_summarised(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await seed(session)

        text = (await api.get(METRICS)).text

        assert sample(text, "stash_transfer_duration_seconds_sum")[""] == 600.0
        assert sample(text, "stash_transfer_duration_seconds_count")[""] == 1.0
        assert sample(text, "stash_queue_wait_seconds_sum")[""] == 120.0

    async def test_the_queue_depth_is_per_storage(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await seed(session)

        depth = sample((await api.get(METRICS)).text, "stash_queue_depth")

        assert depth['{storage="HOT1"}'] == 1.0
        assert depth['{storage="LOC2HOT"}'] == 1.0

    async def test_storage_allocation_and_usage_are_reported(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await seed(session)

        text = (await api.get(METRICS)).text

        assert sample(text, "stash_storage_allocated_bytes")['{storage="LOC2HOT"}'] == float(
            20 * GIB
        )
        assert sample(text, "stash_storage_used_bytes")['{storage="LOC2HOT"}'] == float(
            15 * GIB
        )

    async def test_filesets_are_counted_by_storage_kind_and_state(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await seed(session)

        counted = sample((await api.get(METRICS)).text, "stash_filesets_total")

        assert counted['{kind="cached",state="READY",storage="LOC2HOT"}'] == 1.0

    async def test_daemons_report_whether_they_are_up_and_what_they_run(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        session.add(
            Daemon(
                id="loc2hot",
                storages=["LOC2HOT"],
                version="0.1.0",
                config_revision=42,
                last_seen_at=T0,
            )
        )
        await session.commit()

        text = (await api.get(METRICS)).text

        assert sample(text, "stash_daemon_up")['{daemon="loc2hot"}'] in (0.0, 1.0)
        assert sample(text, "stash_config_revision")['{daemon="loc2hot"}'] == 42.0

    async def test_an_empty_cluster_still_answers(self, api: httpx2.AsyncClient) -> None:
        text = (await api.get(METRICS)).text

        assert "stash_transfers_total" in text
        assert sample(text, "stash_storage_allocated_bytes")['{storage="LOC2HOT"}'] == 0.0
