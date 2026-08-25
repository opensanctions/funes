"""The deferred pipeline task: capture and extract one stored Extraction."""

import asyncio
import logging
import uuid

from openai import OpenAI
from pravda import Snapshot
from procrastinate import App
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from funes import db
from funes.capture import is_blank, pravda_client, read_artifact, storage_filesystem
from funes.config import Config
from funes.extract import Extraction, extract, metadata_from_html, screenshot_reason

log = logging.getLogger("funes")

QUEUE_PIPELINE = "pipeline"
TASK_PROCESS_EXTRACTION = "funes.process_extraction"


def register_tasks(app: App, config: Config) -> None:
    """Register Funes's tasks on *app*, closing over the loaded *config*."""

    @app.task(name=TASK_PROCESS_EXTRACTION, queue=QUEUE_PIPELINE)
    async def process_extraction(extraction_id: str) -> None:
        await run_extraction(config, uuid.UUID(extraction_id))


async def extract_snapshot(
    snapshot: Snapshot,
    fs,
    config: Config,
    client: OpenAI,
) -> Extraction:
    """Extract holder observations from one captured snapshot."""
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
    extraction = await asyncio.to_thread(
        extract,
        client,
        config.model,
        config.image,
        metadata,
        text,
        screenshot_blob,
    )
    log.info(
        "%s → %d person(s), %d position(s)",
        snapshot.url,
        len(extraction.persons),
        sum(len(p.positions) for p in extraction.persons),
    )
    return extraction


async def run_extraction(config: Config, extraction_id: uuid.UUID) -> None:
    """Capture an Extraction's URL and store its extraction result.

    Any failure raises; the job simply fails. No retries, no fallback.
    """
    engine = create_async_engine(config.pravda.database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            extraction = await session.get(db.Extraction, extraction_id)
            if extraction is None:
                raise LookupError(f"extraction {extraction_id} not found")
            await session.commit()

            fs = storage_filesystem(config.pravda)
            pravda = pravda_client(config.pravda)
            async with pravda:
                snapshot = await pravda.snapshot(extraction.url)

            if snapshot.error is not None:
                raise RuntimeError(f"capture failed: {snapshot.error}")
            missing = [
                name
                for name, value in (
                    ("plaintext", snapshot.plaintext),
                    ("rendered HTML", snapshot.rendered_html),
                )
                if value is None
            ]
            if missing:
                raise RuntimeError(
                    "capture missing required artifact metadata: " + ", ".join(missing)
                )

            result = await extract_snapshot(snapshot, fs, config, OpenAI())
            db.extraction_succeeded(session, extraction, snapshot, result)
            await session.commit()
    finally:
        await engine.dispose()
