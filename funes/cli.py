"""Command-line interface for the capture, extraction, and output pipeline."""

import asyncio
import logging
import random
import uuid
from datetime import UTC, datetime

import click
from openai import OpenAI, OpenAIError
from pravda import Snapshot
from pravda import migrate as pravda_migrate
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from funes import db
from funes.capture import (
    OperationalError,
    capture_urls,
    is_blank,
    pravda_client,
    read_artifact,
    storage_filesystem,
    summarise_errors,
)
from funes.config import Config, load_config
from funes.export import holder_to_record, write_outputs
from funes.extract import (
    extract,
    flatten_persons,
    metadata_from_html,
    screenshot_reason,
)
from funes.migrate import migrate
from funes.sources import InputRow, load_inputs

log = logging.getLogger("funes")


@click.group()
def cli() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(logging.FileHandler("funes.log"))
    root.addHandler(logging.StreamHandler())


async def extract_snapshot(
    snapshot: Snapshot,
    fs,
    config: Config,
    client: OpenAI,
) -> list[dict]:
    """Extract flattened holder observations from one captured snapshot."""
    text = (await read_artifact(fs, snapshot.plaintext)).decode(
        "utf-8", errors="replace"
    )
    html = (await read_artifact(fs, snapshot.rendered_html)).decode(
        "utf-8", errors="replace"
    )

    log.info("%s → extracting …", snapshot.url)
    screenshot_blob = None
    reason = screenshot_reason(text, html)
    if reason is not None:
        log.info("  → %s → including screenshot", reason)
        if snapshot.screenshot is not None:
            blob = await read_artifact(fs, snapshot.screenshot)
            if not is_blank(blob):
                screenshot_blob = blob
    metadata = metadata_from_html(snapshot.url, html)
    extraction = extract(
        client,
        config.model,
        config.image,
        metadata,
        text,
        screenshot_blob,
    )
    holders = flatten_persons(extraction)
    log.info("%s → %d holder(s)", snapshot.url, len(holders))
    return holders


async def run_pipeline(
    inputs: list[tuple[str, list[InputRow]]],
    sample: int | None,
    concurrency: int,
    config: Config,
    client: OpenAI,
) -> None:
    """Apply migrations, then capture, extract, and write this run."""
    await pravda_migrate(config.pravda.database_url)
    await migrate(config.pravda.database_url)
    await _run_pipeline(inputs, sample, concurrency, config, client)


async def _run_pipeline(
    inputs: list[tuple[str, list[InputRow]]],
    sample: int | None,
    concurrency: int,
    config: Config,
    client: OpenAI,
) -> None:
    """Persist, capture, and extract the selected input associations."""
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

    urls = sorted({url for _, url, _ in associations})
    log.info("%d unique URL(s) to snapshot", len(urls))

    engine = create_async_engine(config.pravda.database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            run = db.Run(id=uuid.uuid4())
            session.add(run)
            captures = await db.register_pages(session, run, associations)
            await session.commit()
            log.info("run %s: %d capture(s)", run.id, len(captures))

            groups: dict[str, list[dict]] = {dataset: [] for dataset, _ in inputs}
            fs = storage_filesystem(config.pravda)
            pravda = pravda_client(config.pravda)
            async with pravda:
                snapshots, errors = await capture_urls(pravda, urls, concurrency)
                operational_errors = {error.url: error.error for error in errors}
                for url, capture in captures.items():
                    snapshot = snapshots.get(url)
                    if snapshot is None:
                        db.capture_failed(capture, operational_errors[url])
                    elif snapshot.error is not None:
                        db.capture_failed(capture, snapshot.error, snapshot)
                    else:
                        db.capture_succeeded(capture, snapshot)
                await session.commit()

                extracted = 0
                hits = 0
                for dataset, url, organization in associations:
                    snapshot = snapshots.get(url)
                    if snapshot is None:
                        continue
                    if snapshot.error is not None:
                        log.warning(
                            "  skip %s — capture failed: %s", url, snapshot.error
                        )
                        continue
                    missing = [
                        name
                        for name, value in (
                            ("plaintext", snapshot.plaintext),
                            ("rendered HTML", snapshot.rendered_html),
                        )
                        if value is None
                    ]
                    if missing:
                        log.warning(
                            "  skip %s — capture missing required artifact metadata: %s",
                            url,
                            ", ".join(missing),
                        )
                        continue

                    try:
                        holders = await extract_snapshot(snapshot, fs, config, client)
                    except (OpenAIError, OSError, ValidationError) as exc:
                        log.warning(
                            "  extraction failed for %s: %s: %s",
                            url,
                            type(exc).__name__,
                            exc,
                        )
                        errors.append(
                            OperationalError(
                                "extract", url, f"{type(exc).__name__}: {exc}"
                            )
                        )
                        continue
                    groups[dataset].extend(
                        holder_to_record(dataset, url, organization, snapshot, holder)
                        for holder in holders
                    )
                    extracted += 1
                    if holders:
                        hits += 1

            await write_outputs(groups, config.paths)
            run.finished_at = datetime.now(UTC)
            await session.commit()
            log.info("extraction: %d hit, %d miss", hits, extracted - hits)
            summarise_errors(errors)
    finally:
        await engine.dispose()


@cli.command(
    "run",
    help=(
        "Capture URLs from the input CSVs through Pravda, extract position "
        "holders, and write this run's records as per-dataset JSONL. Dataset "
        "filtering and sampling happen before capture; each unique URL is "
        "captured once, concurrently."
    ),
)
@click.option("-d", "--dataset", type=str, default=None, help="Only run this dataset.")
@click.option(
    "-n",
    "--sample",
    type=click.IntRange(min=0),
    default=None,
    help="Randomly sample N page inputs.",
)
@click.option(
    "-c",
    "--concurrency",
    type=click.IntRange(min=1),
    default=5,
    help="Max concurrent Pravda captures.",
)
def run_cmd(dataset: str | None, sample: int | None, concurrency: int) -> None:
    config = load_config()
    client = OpenAI()
    inputs = load_inputs(config.paths.input_base_path)
    if dataset is not None:
        inputs = [(d, rows) for d, rows in inputs if d == dataset]
    log.info("%d input CSV(s)", len(inputs))
    for dataset_name, rows in inputs:
        log.info("dataset %s: %d row(s)", dataset_name, len(rows))
    asyncio.run(run_pipeline(inputs, sample, concurrency, config, client))


if __name__ == "__main__":
    cli()
