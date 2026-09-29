"""Command-line interface for the deferred capture and inspection pipeline."""

import asyncio
import logging

import click
from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from funes import db
from funes.config import load_config
from funes.procrastinate import app
from funes.sources import load_datasets
from funes.tasks import inspect_candidate

log = logging.getLogger("funes")


@click.group()
def cli() -> None:
    logging.basicConfig(level=logging.INFO)


@cli.command(
    help=(
        "Append-only import of dataset/subject/URL candidate rows from "
        "the input YAML datasets."
    )
)
def seed() -> None:
    config = load_config()

    async def run() -> None:
        definitions = load_datasets(config.input_base_path)
        log.info(
            "%d input dataset(s), %d subject(s)",
            len(definitions),
            sum(len(definition.subjects) for definition in definitions),
        )
        engine = create_async_engine(config.database_url)
        try:
            async with async_sessionmaker(engine)() as session:
                await db.import_catalogue(session, definitions)
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(run())


@cli.command(
    help=(
        "Queue one inspection job per due candidate and exit. A candidate "
        "is due when it has never been attempted, or its latest attempt "
        "was a hit inspection older than the revisit interval; candidates "
        "whose latest attempt ended in a miss are not re-enqueued, and "
        "broken snapshots are the repair queue's business."
    )
)
@click.option(
    "--dataset",
    default=None,
    metavar="NAME",
    help="Queue only candidates of this input dataset (e.g. au_judges).",
)
def enqueue_cmd(dataset: str | None) -> None:
    config = load_config()

    async def enqueue() -> None:
        """Select due candidates and queue one job per candidate."""
        engine = create_async_engine(config.database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                if dataset is not None and not await session.scalar(
                    select(db.Dataset.id).where(db.Dataset.name == dataset)
                ):
                    raise click.UsageError(f"unknown dataset: {dataset!r}")
                candidates = await db.select_due_candidates(
                    session, config.revisit_interval, dataset=dataset
                )
        finally:
            await engine.dispose()
        candidate_ids = [str(candidate.id) for candidate in candidates]

        queued = skipped = 0
        async with app.open_async():
            for candidate_id in candidate_ids:
                try:
                    await inspect_candidate.configure(
                        queueing_lock=f"candidate:{candidate_id}"
                    ).defer_async(candidate_id=candidate_id)
                    queued += 1
                except AlreadyEnqueued:
                    # A candidate with a job still pending is skipped so one
                    # stale pending job never aborts the whole sweep.
                    skipped += 1
        log.info(
            "%d due, %d queued, %d already pending",
            len(candidate_ids),
            queued,
            skipped,
        )

    asyncio.run(enqueue())


if __name__ == "__main__":
    cli()
