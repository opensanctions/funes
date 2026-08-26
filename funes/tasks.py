"""Procrastinate app and page capture/extraction task."""

import logging
import uuid

from procrastinate import App, PsycopgConnector
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from funes import db
from funes.capture import (
    first_error_line,
    inspectability_issue,
    pravda_client,
    read_artifact,
    storage_filesystem,
)
from funes.config import Config
from funes.extract import (
    BrokenPage,
    build_extraction_agent,
    metadata_from_html,
    prompt_content,
)
from funes.sessions import session_path, write_session

log = logging.getLogger("funes")

QUEUE_PIPELINE = "pipeline"
QUEUE_BROKEN = "broken"
TASK_PROCESS_PAGE = "funes.process_page"
TASK_REVIEW_BROKEN_PAGE = "funes.review_broken_page"


def build_app(config: Config) -> App:
    """Construct the Procrastinate app and register its tasks.

    Tasks must be defined inside this factory so that each build_app call
    registers them on its own App instance.
    """
    dsn = (
        make_url(config.pravda.database_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    app: App = App(connector=PsycopgConnector(conninfo=dsn))

    @app.task(name=TASK_REVIEW_BROKEN_PAGE, queue=QUEUE_BROKEN)
    async def review_broken_page(
        page_id: str, snapshot_id: str, run_id: str, reason: str
    ) -> None:
        raise NotImplementedError(
            "broken-page review is not implemented yet "
            f"(page {page_id}, snapshot {snapshot_id}, run {run_id})"
        )

    @app.task(name=TASK_PROCESS_PAGE, queue=QUEUE_PIPELINE)
    async def process_page(page_id: str) -> None:
        """Capture one Page's URL and store the extraction result.

        Procrastinate is the pending/running/failure ledger; the database only ever sees
        committed successful extraction graphs. Broken pages are routed to
        a dormant review queue whose payload (page, snapshot, run, reason)
        is the durable routing record.
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

                issue = inspectability_issue(snapshot)
                if issue is not None:
                    await review_broken_page.defer_async(
                        page_id=page_id,
                        snapshot_id=str(snapshot.id),
                        run_id=str(extraction_id),
                        reason=issue,
                    )
                    log.info("%s → broken: %s", url, issue)
                    return

                text = (await read_artifact(fs, snapshot.plaintext)).decode("utf-8")
                html = (await read_artifact(fs, snapshot.rendered_html)).decode("utf-8")

                metadata = metadata_from_html(
                    url,
                    html,
                    final_url=snapshot.final_url,
                    http_status=snapshot.http_status,
                    capture_error=first_error_line(snapshot.error),
                )
                log.info("%s → extracting …", snapshot.final_url)
                agent = build_extraction_agent(model)
                result = await agent.run(
                    prompt_content(metadata, text),
                    run_id=str(extraction_id),
                )
                session_file = session_path(
                    config.sessions.base_path, str(extraction_id)
                )

                if isinstance(result.output, BrokenPage):
                    reason = result.output.reason
                    write_session(session_file, result.all_messages())
                    await review_broken_page.defer_async(
                        page_id=page_id,
                        snapshot_id=str(snapshot.id),
                        run_id=str(extraction_id),
                        reason=reason,
                    )
                    log.info("%s → broken: %s", snapshot.final_url, reason)
                    return

                extraction_result = result.output
                log.info(
                    "%s → %d person(s), %d position(s)",
                    snapshot.final_url,
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
                write_session(session_file, result.all_messages())
                await session.commit()
        finally:
            await engine.dispose()

    return app
