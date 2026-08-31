"""A real PostgreSQL for the integration suite.

The database named by ``STASH_TEST_DATABASE_URL`` (default: the one in compose.yaml) is
created once per session and migrated with Alembic; every test starts from empty tables.
"""

import os
from collections.abc import AsyncIterator, Iterator

import httpx2
import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from stashd.api.app import create_app
from stashd.auth.fake import FakeAuthProvider
from stashd.config.bootstrap import BootstrapConfig
from stashd.config.cluster import ClusterConfig
from stashd.db import create_engine, session_factory
from stashd.domain.identity import Principal
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


@pytest_asyncio.fixture
async def api(
    sessions: async_sessionmaker[AsyncSession],
    controller_bootstrap: BootstrapConfig,
    cluster_config: ClusterConfig,
) -> AsyncIterator[httpx2.AsyncClient]:
    """The controller with a real database and a fake credential per user."""
    app = create_app(
        controller_bootstrap,
        cluster_config,
        auth=FakeAuthProvider(CREDENTIALS),
        sessions=sessions,
    )
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://controller/api/v1"
    ) as client:
        client.headers["Authorization"] = "Munge cred-user"
        yield client
