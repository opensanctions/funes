"""Apply Funes's packaged Alembic migrations."""

import asyncio
from importlib.resources import files

from alembic import command
from alembic.config import Config

VERSION_TABLE = "funes_alembic_version"


def _config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(files("funes") / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


async def migrate(database_url: str) -> None:
    """Upgrade the Funes schema to its packaged migration head."""
    await asyncio.to_thread(command.upgrade, _config(database_url), "head")
