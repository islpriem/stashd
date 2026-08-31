"""Alembic environment.

The database URL comes from ``STASH_DATABASE_URL`` or from a bootstrap config named with
``-x config=dev/controller.yaml``; migrations run with the synchronous psycopg driver.
"""

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

from stashd.config.bootstrap import load_bootstrap_config
from stashd.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    """-x url=..., then a url set by the caller, then the environment, then -x config=..."""
    arguments = context.get_x_argument(as_dictionary=True)
    if "url" in arguments:
        return str(arguments["url"])
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    from_environment = os.environ.get("STASH_DATABASE_URL")
    if from_environment:
        return from_environment
    if "config" in arguments:
        bootstrap = load_bootstrap_config(Path(arguments["config"]))
        if bootstrap.database is not None:
            return bootstrap.database.url
    raise SystemExit("set STASH_DATABASE_URL or pass -x config=<bootstrap config>")


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
