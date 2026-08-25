"""Apply Funes's packaged Alembic migrations."""

import asyncio
from importlib.resources import files

from alembic import command
from alembic.config import Config


async def migrate(database_url: str) -> None:
    """Upgrade the Funes schema to its packaged migration head."""
    config = Config()
    config.set_main_option("script_location", str(files("funes") / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    await asyncio.to_thread(command.upgrade, config, "head")
