"""Every migration must come back down again."""

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from tests.integration.conftest import admin_url, url_for

pytestmark = pytest.mark.integration

DATABASE = "stash_migration_test"


@pytest.fixture
def scratch_database() -> Iterator[str]:
    admin = sa.create_engine(admin_url(), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(sa.text(f"DROP DATABASE IF EXISTS {DATABASE}"))
        connection.execute(sa.text(f"CREATE DATABASE {DATABASE}"))
    yield url_for(DATABASE)
    with admin.connect() as connection:
        connection.execute(sa.text(f"DROP DATABASE IF EXISTS {DATABASE}"))
    admin.dispose()


def table_names(url: str) -> set[str]:
    engine = sa.create_engine(url)
    try:
        return set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_the_schema_upgrades_downgrades_and_upgrades_again(scratch_database: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", scratch_database)

    command.upgrade(config, "head")
    assert "filesets" in table_names(scratch_database)

    command.downgrade(config, "base")
    assert table_names(scratch_database) == {"alembic_version"}

    command.upgrade(config, "head")
    assert "transfers" in table_names(scratch_database)
