"""Procrastinate tasks for the page capture and extraction pipeline."""

import logging
import uuid
from enum import StrEnum
from functools import partial

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from funes import db
from funes.capture import (
    artifact_filesystem,
    first_error_line,
    inspectability_issue,
    pravda_client,
    read_artifact,
)
from funes.extract import (
    BrokenPage,
    ExtractionDependencies,
    build_extraction_agent,
    build_prompt,
    metadata_from_html,
)
from funes.outline import build_outline, har_resource_media_types
from funes.procrastinate import app, config
from funes.sessions import session_path, write_session

log = logging.getLogger("funes")


class Queue(StrEnum):
    """Procrastinate queues, named for the work they carry."""

    PROCESS = "process"
    REVIEW = "review"


class Task(StrEnum):
    """Registered Procrastinate task names."""

    PROCESS_PAGE = "funes.process_page"
    REVIEW_BROKEN_PAGE = "funes.review_broken_page"


@app.task(name=Task.REVIEW_BROKEN_PAGE, queue=Queue.REVIEW)
async def review_broken_page(
    page_id: str, snapshot_id: str, run_id: str, reason: str
) -> None:
    raise NotImplementedError(
        "broken-page review is not implemented yet "
        f"(page {page_id}, snapshot {snapshot_id}, run {run_id})"
    )


@app.task(name=Task.PROCESS_PAGE, queue=Queue.PROCESS)
async def process_page(page_id: str) -> None:
    """Capture one Page's URL and store the extraction result.

    Procrastinate is the pending/running/failure ledger; the database only
    ever sees committed successful extraction graphs. Broken pages are
    routed to a dormant review queue whose payload (page, snapshot, run,
    reason) is the durable routing record.
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
            url = page.url
            # End the read transaction before capture and LLM work;
            # expire_on_commit=False keeps the loaded attributes alive.
            await session.commit()

            fs = artifact_filesystem(config.pravda)
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

            html = (await read_artifact(fs, snapshot.rendered_html)).decode("utf-8")

            metadata = metadata_from_html(
                url,
                html,
                final_url=snapshot.final_url,
                http_status=snapshot.http_status,
                capture_error=first_error_line(snapshot.error),
            )
            outline = build_outline(snapshot.final_url, html, snapshot.http_archive)
            deps = ExtractionDependencies(
                read_resource=partial(read_artifact, fs),
                resource_media_types=har_resource_media_types(snapshot.http_archive),
            )
            log.info("%s → extracting …", snapshot.final_url)
            agent = build_extraction_agent(model)
            result = await agent.run(
                build_prompt(metadata, outline),
                run_id=str(extraction_id),
                deps=deps,
            )
            session_file = session_path(config.sessions.base_path, str(extraction_id))

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
                page_id=page.id,
                model=model,
                snapshot=snapshot,
                result=extraction_result,
            )
            write_session(session_file, result.all_messages())
            await session.commit()
    finally:
        await engine.dispose()
