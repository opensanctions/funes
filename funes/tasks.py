"""Procrastinate wiring: the app factory and the single pipeline task.

``build_app`` owns both the app and its one task, so the whole pipeline —
load the Extraction, capture the URL with Pravda, run the extraction agent,
persist the result — reads top to bottom inside the task body. Any failure
raises; the job simply fails. No retries, no fallback.
"""

import logging
import uuid

from procrastinate import App, PsycopgConnector
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from funes import db
from funes.capture import (
    is_blank,
    pravda_client,
    read_artifact,
    split_image,
    storage_filesystem,
)
from funes.config import Config
from funes.extract import (
    _INSTRUCTIONS_WITH_SCREENSHOT,
    build_extraction_agent,
    metadata_from_html,
    prompt_content,
    screenshot_reason,
)
from funes.sessions import session_path, write_session

log = logging.getLogger("funes")

QUEUE_PIPELINE = "pipeline"
TASK_PROCESS_EXTRACTION = "funes.process_extraction"


def build_app(config: Config) -> App:
    """Construct the Procrastinate app with Funes's single pipeline task.

    Takes an already-loaded Config so no env vars are read at import time;
    calling this repeatedly yields equivalent, independent apps.
    """
    dsn = (
        make_url(config.pravda.database_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    app: App = App(connector=PsycopgConnector(conninfo=dsn))

    @app.task(name=TASK_PROCESS_EXTRACTION, queue=QUEUE_PIPELINE)
    async def process_extraction(extraction_id: str) -> None:
        """Capture one Extraction's URL and store the extraction result."""
        engine = create_async_engine(config.pravda.database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                extraction = await session.get(db.Extraction, uuid.UUID(extraction_id))
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
                        "capture missing required artifact metadata: "
                        + ", ".join(missing)
                    )

                text = (await read_artifact(fs, snapshot.plaintext)).decode(
                    "utf-8", errors="replace"
                )
                html = (await read_artifact(fs, snapshot.rendered_html)).decode(
                    "utf-8", errors="replace"
                )

                log.info("%s → extracting …", snapshot.url)
                tiles: list[bytes] = []
                reason = screenshot_reason(text, html)
                if reason is not None:
                    log.info("  → %s → including screenshot", reason)
                    if snapshot.screenshot is not None:
                        blob = await read_artifact(fs, snapshot.screenshot)
                        if not is_blank(blob):
                            tiles = split_image(
                                blob,
                                config.image.tile_size,
                                config.image.tile_overlap,
                            )
                agent = build_extraction_agent(extraction.model)
                result = await agent.run(
                    prompt_content(metadata_from_html(snapshot.url, html), text, tiles),
                    run_id=extraction_id,
                    instructions=_INSTRUCTIONS_WITH_SCREENSHOT if tiles else None,
                )
                extraction_result = result.output
                log.info(
                    "%s → %d person(s), %d position(s)",
                    snapshot.url,
                    len(extraction_result.persons),
                    sum(len(p.positions) for p in extraction_result.persons),
                )
                db.extraction_succeeded(
                    session, extraction, snapshot, extraction_result
                )
                write_session(
                    session_path(config.sessions.base_path, extraction_id),
                    result.all_messages(),
                )
                await session.commit()
        finally:
            await engine.dispose()

    return app
