"""Online-only Alembic environment for Funes."""

import asyncio
from logging.config import fileConfig

from alembic import context
from pravda.db import Base as PravdaBase
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from funes.config import load_config
from funes.db import Base as FunesBase

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = [FunesBase.metadata, PravdaBase.metadata]


def include_object(obj, name, type_, reflected, compare_to):
    """Exclude reflected tables absent from metadata (Procrastinate's)."""
    return not (type_ == "table" and reflected and compare_to is None)


def run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = create_async_engine(
        load_config().pravda.database_url, poolclass=pool.NullPool
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise ValueError("Offline migrations are not supported")

asyncio.run(run_online())
