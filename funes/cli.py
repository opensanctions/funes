"""Command-line interface for the deferred capture and extraction pipeline."""

import asyncio
import logging
import random

import click
from pravda import migrate as pravda_migrate
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from funes import db
from funes.config import Config, load_config
from funes.migrate import migrate
from funes.queue import QUEUE_PIPELINE, TASK_PROCESS_EXTRACTION, build_app
from funes.sources import InputRow, load_inputs

log = logging.getLogger("funes")


@click.group()
def cli() -> None:
    logging.basicConfig(level=logging.INFO)


@cli.command(help="Apply Pravda's packaged migrations, then Funes's own.")
def migrate_cmd() -> None:
    config = load_config()
    asyncio.run(_migrate(config))


async def _migrate(config: Config) -> None:
    await pravda_migrate(config.pravda.database_url)
    await migrate(config.pravda.database_url)


@cli.command(
    help=(
        "Load, filter, and sample inputs from the input CSVs, persist one "
        "Extraction (with its Pages) per unique URL, then queue exactly one "
        "pipeline job per Extraction and exit."
    )
)
@click.option("-d", "--dataset", type=str, default=None, help="Only run this dataset.")
@click.option(
    "-n",
    "--sample",
    type=click.IntRange(min=0),
    default=None,
    help="Randomly sample N page inputs.",
)
def enqueue_cmd(dataset: str | None, sample: int | None) -> None:
    config = load_config()
    inputs = load_inputs(config.input.input_base_path)
    if dataset is not None:
        inputs = [(d, rows) for d, rows in inputs if d == dataset]
    log.info("%d input CSV(s)", len(inputs))
    for dataset_name, rows in inputs:
        log.info("dataset %s: %d row(s)", dataset_name, len(rows))
    asyncio.run(_enqueue(inputs, sample, config))


async def _enqueue(
    inputs: list[tuple[str, list[InputRow]]],
    sample: int | None,
    config: Config,
) -> None:
    """Persist the selected associations and queue one job per Extraction."""
    associations: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for dataset, rows in inputs:
        for row in rows:
            key = (dataset, row.url)
            if key in seen:
                continue
            seen.add(key)
            associations.append((dataset, row.url, row.organization))

    if sample is not None and sample < len(associations):
        associations = random.sample(associations, sample)
    log.info("%d page association(s) selected", len(associations))

    app = build_app(config)
    engine = create_async_engine(config.pravda.database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            extractions = await db.register_extractions(
                session, associations, config.model.name
            )
            await session.commit()
            log.info("%d extraction(s) registered", len(extractions))

            async with app.open_async():
                await app.configure_task(
                    TASK_PROCESS_EXTRACTION, allow_unknown=False
                ).batch_defer_async(
                    *(
                        {"extraction_id": str(extraction.id)}
                        for extraction in extractions.values()
                    )
                )
            log.info("%d job(s) queued", len(extractions))
    finally:
        await engine.dispose()


@cli.command(
    help="Consume the pipeline queue and process extractions.",
)
@click.option(
    "-c",
    "--concurrency",
    type=click.IntRange(min=1),
    default=1,
    help="Concurrent jobs per worker.",
)
def worker_cmd(concurrency: int) -> None:
    config = load_config()
    app = build_app(config)
    app.run_worker(queues=[QUEUE_PIPELINE], concurrency=concurrency)


if __name__ == "__main__":
    cli()
