"""Online-only Alembic environment for Funes."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from funes.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def include_funes_name(
    name: str | None,
    type_: str,
    parent_names: dict[str, str | None],
) -> bool:
    """Limit schema comparison to tables declared by Funes."""
    if type_ == "table":
        return parent_names["schema_qualified_table_name"] in Base.metadata.tables
    return True


def run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        version_table="funes_alembic_version",
        include_name=include_funes_name,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    database_url = config.get_main_option("sqlalchemy.url")
    if not database_url:
        raise RuntimeError("No database URL configured")
    engine = create_async_engine(database_url, poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise ValueError("Offline migrations are not supported")

asyncio.run(run_online())
