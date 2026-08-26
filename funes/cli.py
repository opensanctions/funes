"""Command-line interface for the deferred capture and extraction pipeline."""

import asyncio
import logging

import click
from pravda import migrate as pravda_migrate
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from funes import db
from funes.config import load_config
from funes.migrate import migrate
from funes.procrastinate import app
from funes.sources import load_inputs
from funes.tasks import process_page

log = logging.getLogger("funes")


@click.group()
def cli() -> None:
    logging.basicConfig(level=logging.INFO)


@cli.command(
    help=(
        "Apply Pravda's packaged migrations, then Funes's own, then "
        "append-only import URL/organization pairs from the input CSVs."
    ),
)
def migrate_cmd() -> None:
    config = load_config()

    async def run() -> None:
        await pravda_migrate(config.pravda.database_url)
        await migrate(config.pravda.database_url)

        rows = load_inputs(config.input.base_path)
        log.info("%d input row(s)", len(rows))
        engine = create_async_engine(config.pravda.database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                await db.import_pages(session, rows)
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(run())


@cli.command(
    help=("Queue exactly one pipeline job per page recorded in the database and exit.")
)
def enqueue_cmd() -> None:
    config = load_config()

    async def enqueue() -> None:
        """Select pages from the database and queue one job per page."""
        engine = create_async_engine(config.pravda.database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                pages = await db.select_pages(session)
            page_ids = [str(page.id) for page in pages]
        finally:
            await engine.dispose()

        log.info("%d page(s) selected", len(page_ids))
        async with app.open_async():
            await process_page.batch_defer_async(
                *({"page_id": page_id} for page_id in page_ids)
            )
        log.info("%d job(s) queued", len(page_ids))

    asyncio.run(enqueue())


if __name__ == "__main__":
    cli()
