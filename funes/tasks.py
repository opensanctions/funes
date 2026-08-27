"""Procrastinate tasks for the candidate inspection and repair pipeline."""

import logging
import uuid
from enum import StrEnum
from functools import partial

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from funes import db
from funes.capture import (
    artifact_filesystem,
    first_error_line,
    inspectability_issue,
    pravda_client,
    read_artifact,
)
from funes.extract import (
    BrokenSnapshot,
    ExtractionDependencies,
    Hit,
    Miss,
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
    REPAIR = "repair"


class Task(StrEnum):
    """Registered Procrastinate task names."""

    INSPECT_CANDIDATE = "funes.inspect_candidate"
    REPAIR_SNAPSHOT = "funes.repair_snapshot"


@app.task(name=Task.REPAIR_SNAPSHOT, queue=Queue.REPAIR)
async def repair_snapshot(attempt_id: str) -> None:
    raise NotImplementedError(
        f"broken-snapshot repair is not implemented yet (attempt {attempt_id})"
    )


@app.task(name=Task.INSPECT_CANDIDATE, queue=Queue.PROCESS)
async def inspect_candidate(candidate_id: str) -> None:
    """Capture one Candidate's URL and judge it against its objective.

    Procrastinate is the pending/running/failure ledger; the database
    records exactly one terminal aggregate per completed run (a Hit/Miss
    Inspection or a broken SnapshotAssessment). Infra crashes
    (exceptions) write no row — retrying those is Procrastinate's
    business, not a candidate fact. Broken snapshots are additionally
    routed to a dormant repair queue whose payload is only the completed
    attempt id: repair can navigate attempt → candidate → URL and
    assessment → snapshot; the attempt row is the durable record.
    """
    # The model comes from worker configuration, never from the queue
    # payload; the attempt UUID is generated here and identifies the run:
    # Pydantic run_id, transcript basename, DB Attempt.id, repair routing.
    model = config.model.name
    attempt_id = uuid.uuid4()

    engine = create_async_engine(config.pravda.database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            candidate = await session.get(
                db.Candidate,
                uuid.UUID(candidate_id),
                options=(
                    selectinload(db.Candidate.objective),
                    selectinload(db.Candidate.url),
                ),
            )
            if candidate is None:
                raise LookupError(f"candidate {candidate_id} not found")
            url = candidate.url.url
            objective = candidate.objective.description
            # End the read transaction before capture and LLM work;
            # expire_on_commit=False keeps the loaded attributes alive.
            await session.commit()

            fs = artifact_filesystem(config.pravda)
            pravda = pravda_client(config.pravda)
            async with pravda:
                snapshot = await pravda.snapshot(url)

            issue = inspectability_issue(snapshot)
            if issue is not None:
                db.store_broken_attempt(
                    session,
                    attempt_id=attempt_id,
                    candidate_id=candidate.id,
                    snapshot=snapshot,
                    reason=issue,
                )
                await session.commit()
                await repair_snapshot.defer_async(attempt_id=str(attempt_id))
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
            run = await agent.run(
                build_prompt(objective, metadata, outline),
                run_id=str(attempt_id),
                deps=deps,
            )
            session_file = session_path(config.sessions.base_path, str(attempt_id))

            if isinstance(run.output, BrokenSnapshot):
                reason = run.output.reason
                write_session(session_file, run.all_messages())
                db.store_broken_attempt(
                    session,
                    attempt_id=attempt_id,
                    candidate_id=candidate.id,
                    snapshot=snapshot,
                    reason=reason,
                    model=model,
                )
                await session.commit()
                await repair_snapshot.defer_async(attempt_id=str(attempt_id))
                log.info("%s → broken: %s", snapshot.final_url, reason)
                return

            write_session(session_file, run.all_messages())
            match run.output:
                case Hit() as hit:
                    log.info(
                        "%s → hit: %d person(s), %d position(s)",
                        snapshot.final_url,
                        len(hit.persons),
                        sum(len(person.positions) for person in hit.persons),
                    )
                case Miss() as miss:
                    log.info("%s → miss: %s", snapshot.final_url, miss.reason)
            db.store_inspection(
                session,
                attempt_id=attempt_id,
                candidate_id=candidate.id,
                snapshot=snapshot,
                result=run.output,
                model=model,
            )
            await session.commit()
    finally:
        await engine.dispose()
