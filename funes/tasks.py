"""Procrastinate app and page capture/extraction task."""

import logging
import uuid

from procrastinate import App, PsycopgConnector
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from funes import db
from funes.capture import pravda_client, read_artifact, storage_filesystem
from funes.config import Config
from funes.extract import (
    build_extraction_agent,
    metadata_from_html,
    prompt_content,
)
from funes.sessions import session_path, write_session

log = logging.getLogger("funes")

QUEUE_PIPELINE = "pipeline"
TASK_PROCESS_PAGE = "funes.process_page"


def build_app(config: Config) -> App:
    """Construct the Procrastinate app and register its page task."""
    dsn = (
        make_url(config.pravda.database_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    app: App = App(connector=PsycopgConnector(conninfo=dsn))

    @app.task(name=TASK_PROCESS_PAGE, queue=QUEUE_PIPELINE)
    async def process_page(page_id: str) -> None:
        """Capture one Page's URL and store the extraction result.

        Procrastinate is the pending/running/failure ledger; the database
        only ever sees committed successful extraction graphs.
        """
        # The model comes from worker configuration, never from the queue
        # payload; the extraction UUID is generated here to identify the run.
        model = config.model.name
        extraction_id = uuid.uuid4()

        engine = create_async_engine(config.pravda.database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                page = await session.get(db.Page, uuid.UUID(page_id))
                if page is None:
                    raise LookupError(f"page {page_id} not found")
                page_pk, url = page.id, page.url
                # Close the read transaction before capture and LLM work.
                await session.commit()

                fs = storage_filesystem(config.pravda)
                pravda = pravda_client(config.pravda)
                async with pravda:
                    snapshot = await pravda.snapshot(url)

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
                agent = build_extraction_agent(model)
                result = await agent.run(
                    prompt_content(metadata_from_html(snapshot.url, html), text),
                    run_id=str(extraction_id),
                )
                extraction_result = result.output
                log.info(
                    "%s → %d person(s), %d position(s)",
                    snapshot.url,
                    len(extraction_result.persons),
                    sum(len(p.positions) for p in extraction_result.persons),
                )
                db.store_extraction(
                    session,
                    extraction_id=extraction_id,
                    page_id=page_pk,
                    model=model,
                    snapshot=snapshot,
                    result=extraction_result,
                )
                write_session(
                    session_path(config.sessions.base_path, str(extraction_id)),
                    result.all_messages(),
                )
                await session.commit()
        finally:
            await engine.dispose()

    return app
