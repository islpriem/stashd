"""A real PostgreSQL for the integration suite.

The database named by ``STASH_TEST_DATABASE_URL`` (default: the one in compose.yaml) is
created once per session and migrated with Alembic; every test starts from empty tables.
"""

import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from stashd.api.app import create_app
from stashd.auth.fake import FakeAuthProvider
from stashd.auth.token import TokenAuthProvider
from stashd.clients.transfers import ProbeResult, StartedTask, TaskState
from stashd.config.bootstrap import BootstrapConfig
from stashd.config.cluster import ClusterConfig
from stashd.db import create_engine, session_factory
from stashd.domain.errors import NotFound
from stashd.domain.identity import Principal
from stashd.domain.storage import FilesetLocation, Owner
from stashd.drivers.fake import FakeDriver
from stashd.engines.base import TransferEndpoint
from stashd.models import Base

USER = Principal(uid=1000, gid=1000, username="mmustermann", groups=("users",), gids=(1000,))
OTHER = Principal(uid=1001, gid=1001, username="jdoe", groups=("users",), gids=(1001,))
ADMIN = Principal(uid=0, gid=0, username="root")
CREDENTIALS = {"cred-user": USER, "cred-other": OTHER, "cred-admin": ADMIN}

DEFAULT_URL = "postgresql+psycopg://stash:stash@localhost:5432/stash"
TEST_DATABASE = "stash_test"


def admin_url() -> str:
    """The database used only to create and drop the test databases."""
    return os.environ.get("STASH_TEST_DATABASE_URL", DEFAULT_URL)


def url_for(database: str) -> str:
    return admin_url().rsplit("/", 1)[0] + f"/{database}"


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[str]:
    admin = sa.create_engine(admin_url(), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(sa.text(f"DROP DATABASE IF EXISTS {TEST_DATABASE}"))
            connection.execute(sa.text(f"CREATE DATABASE {TEST_DATABASE}"))
    except sa.exc.OperationalError as error:  # pragma: no cover - depends on the environment
        pytest.skip(f"no PostgreSQL at {admin_url()}: {error}")
    finally:
        admin.dispose()

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url_for(TEST_DATABASE))
    command.upgrade(config, "head")
    yield url_for(TEST_DATABASE)


@pytest_asyncio.fixture
async def engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(migrated_database)
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    async with engine.begin() as connection:
        await connection.execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return session_factory(engine)


@pytest_asyncio.fixture
async def session(sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with sessions() as session:
        yield session


class DirectFilesetStore:
    """A store that calls a driver in-process: what the daemon does, without the hop."""

    def __init__(self, driver: FakeDriver) -> None:
        self.driver = driver

    async def create(
        self, storage_id: str, owner: Owner, name: str, allocation_bytes: int
    ) -> FilesetLocation:
        location = self.driver.create_fileset(owner, name, allocation_bytes)
        self.driver.set_fileset_quota(location, allocation_bytes)
        return location

    async def delete(self, fileset_id: int, location: FilesetLocation) -> None:
        self.driver.delete_fileset(location)

    async def set_quota(self, location: FilesetLocation, allocation_bytes: int) -> None:
        self.driver.set_fileset_quota(location, allocation_bytes)


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class FakeOwnerLookup:
    """Everyone the tests know about, without touching the host's passwd file."""

    def by_name(self, user: str) -> Owner:
        known = {"mmustermann": (1000, 1000), "jdoe": (1001, 1001), "root": (0, 0)}
        if user not in known:
            raise NotFound(f"no user named {user}", user=user)
        uid, gid = known[user]
        return Owner(user=user, uid=uid, gid=gid)


class FakeDispatcher:
    """Stands in for the daemons: what was probed, prepared and started."""

    def __init__(self) -> None:
        self.exists = True
        self.readable = True
        self.bytes_total = 20 * 1024**3
        self.file_count = 12043
        self.prepared: list[tuple[str, str]] = []
        self.started: list[dict[str, Any]] = []
        self.fail_start: Exception | None = None
        self.fail_start_once: Exception | None = None
        self.task_states: dict[str, dict[str, Any]] = {}
        self.fail_task_state = False
        self.aborted: list[tuple[str, str]] = []
        self.fail_abort: Exception | None = None

    async def probe(self, storage_id: str, owner: Owner, path: str) -> ProbeResult:
        return ProbeResult(
            exists=self.exists,
            is_dir=True,
            readable=self.readable,
            bytes_total=self.bytes_total,
            file_count=self.file_count,
            complete=True,
        )

    async def task_state(self, storage_id: str, task_id: str) -> TaskState:
        from stashd.clients.filesets import DaemonUnavailable

        if self.fail_task_state:
            raise DaemonUnavailable(f"{storage_id} is unreachable", daemon="hot1")
        found = self.task_states.get(task_id)
        if found is None:
            raise DaemonUnavailable(f"no task {task_id}", daemon="hot1")
        return TaskState(
            state=str(found["state"]),
            bytes_done=int(found.get("bytes_done", 0)),
            files_done=int(found.get("files_done", 0)),
            failure=found.get("failure"),
            message=str(found.get("message", "")),
        )

    async def abort(self, storage_id: str, task_id: str) -> None:
        if self.fail_abort is not None:
            raise self.fail_abort
        self.aborted.append((storage_id, task_id))

    async def prepare(
        self, storage_id: str, owner: Owner, name: str, allocation_bytes: int
    ) -> TransferEndpoint:
        self.prepared.append((storage_id, name))
        return TransferEndpoint(path=f"/fake/cache/{owner.user}/{name}")

    async def start(
        self,
        storage_id: str,
        *,
        transfer_id: int,
        source_path: str,
        owner: Owner,
        target: TransferEndpoint,
        bwlimit_bytes_per_s: int | None,
        delete: bool,
    ) -> StartedTask:
        if self.fail_start is not None:
            raise self.fail_start
        if self.fail_start_once is not None:
            failure, self.fail_start_once = self.fail_start_once, None
            raise failure
        self.started.append(
            {
                "storage_id": storage_id,
                "transfer_id": transfer_id,
                "source_path": source_path,
                "target": target.path,
                "target_host": target.host,
                "target_user": target.user,
                "bwlimit_bytes_per_s": bwlimit_bytes_per_s,
                "delete": delete,
            }
        )
        return StartedTask(task_id=f"task-{transfer_id}", daemon_id="hot1")


@pytest.fixture
def dispatcher() -> FakeDispatcher:
    return FakeDispatcher()


@pytest.fixture
def fake_driver() -> FakeDriver:
    return FakeDriver(storage_id="LOC2HOT", fileset_prefix="/fake/cache")


PEER_TOKEN = "peer-s3cret"


@pytest_asyncio.fixture
async def peer(
    sessions: async_sessionmaker[AsyncSession],
    controller_bootstrap: BootstrapConfig,
    cluster_config: ClusterConfig,
    fake_driver: FakeDriver,
) -> AsyncIterator[httpx2.AsyncClient]:
    """The controller as a daemon sees it: peer token, internal API."""
    app = create_app(
        controller_bootstrap,
        cluster_config,
        auth=FakeAuthProvider(CREDENTIALS),
        sessions=sessions,
        store=DirectFilesetStore(fake_driver),
        owners=FakeOwnerLookup(),
        clock=FixedClock(datetime(2026, 9, 1, 12, 0, tzinfo=UTC)),
        peer_auth=TokenAuthProvider(PEER_TOKEN),
    )
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://controller/internal/v1"
    ) as client:
        client.headers["Authorization"] = f"Bearer {PEER_TOKEN}"
        yield client


@pytest_asyncio.fixture
async def api(
    sessions: async_sessionmaker[AsyncSession],
    controller_bootstrap: BootstrapConfig,
    cluster_config: ClusterConfig,
    fake_driver: FakeDriver,
    dispatcher: FakeDispatcher,
) -> AsyncIterator[httpx2.AsyncClient]:
    """The controller with a real database and a fake credential per user."""
    app = create_app(
        controller_bootstrap,
        cluster_config,
        auth=FakeAuthProvider(CREDENTIALS),
        sessions=sessions,
        store=DirectFilesetStore(fake_driver),
        dispatcher=dispatcher,
        owners=FakeOwnerLookup(),
        clock=FixedClock(datetime(2026, 9, 1, 12, 0, tzinfo=UTC)),
    )
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://controller/api/v1"
    ) as client:
        client.headers["Authorization"] = "Munge cred-user"
        yield client
