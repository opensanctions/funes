"""Command-line interface for the deferred capture and extraction pipeline."""

import asyncio
import logging

import click
from pravda import migrate as pravda_migrate
from procrastinate.exceptions import AlreadyEnqueued
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
        "append-only import dataset/URL/organization rows from the input CSVs."
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
    help=(
        "Queue one pipeline job per due page and exit. A page is due when it "
        "has never been inspected, or its latest inspection was productive "
        "and older than the revisit interval; empty and broken pages are "
        "not re-enqueued."
    )
)
def enqueue_cmd() -> None:
    config = load_config()

    async def enqueue() -> None:
        """Select due pages from the database and queue one job per page."""
        engine = create_async_engine(config.pravda.database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                pages = await db.select_due_pages(session, config.revisit_interval)
            page_ids = [str(page.id) for page in pages]
        finally:
            await engine.dispose()

        queued = skipped = 0
        async with app.open_async():
            for page_id in page_ids:
                try:
                    await process_page.configure(
                        queueing_lock=f"page:{page_id}"
                    ).defer_async(page_id=page_id)
                    queued += 1
                except AlreadyEnqueued:
                    # A page with a job still pending is skipped so one stale
                    # pending job never aborts the whole sweep.
                    skipped += 1
        log.info(
            "%d due, %d queued, %d already pending", len(page_ids), queued, skipped
        )

    asyncio.run(enqueue())


if __name__ == "__main__":
    cli()
