"""Procrastinate tasks for the candidate inspection and repair pipeline."""

import logging
import uuid
from enum import StrEnum
from functools import partial

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from funes import db
from funes.agents import Brief
from funes.capture import (
    artifact_filesystem,
    first_error_line,
    inspectability_issue,
    pravda_client,
    read_artifact,
)
from funes.discovery import discovery_agent, page_link_urls
from funes.extract import (
    BrokenSnapshot,
    ExtractionDependencies,
    Hit,
    Miss,
    build_prompt,
    extraction_agent,
    metadata_from_html,
)
from funes.outline import (
    build_outline,
    har_resource_media_types,
)
from funes.procrastinate import app, config
from funes.sessions import session_path, write_session

log = logging.getLogger("funes")


class Queue(StrEnum):
    """Procrastinate queues, named for the work they carry."""

    INSPECT = "inspect"
    DISCOVERY = "discovery"
    REPAIR = "repair"


class Task(StrEnum):
    """Registered Procrastinate task names."""

    INSPECT_CANDIDATE = "funes.inspect_candidate"
    DISCOVER_LINKS = "funes.discover_links"
    REPAIR_SNAPSHOT = "funes.repair_snapshot"


@app.task(name=Task.REPAIR_SNAPSHOT, queue=Queue.REPAIR)
async def repair_snapshot(attempt_id: str) -> None:
    raise NotImplementedError(
        f"broken-snapshot repair is not implemented yet (attempt {attempt_id})"
    )


@app.task(name=Task.DISCOVER_LINKS, queue=Queue.DISCOVERY)
async def discover_links(attempt_id: str) -> None:
    """Discover follow-up candidates from one completed usable attempt.

    Runs in a fresh agent context on the dedicated discovery queue: it
    reloads the attempt with its candidate, URL, subject, dataset brief,
    and inspection, reconstructs the exact snapshot Pravda stored for
    it, and lets the discovery agent select follow-up links from the same
    page snapshot prompt the extraction agent judged.
    Broken attempts have no inspection and are never routed here.
    """
    model = config.model.name

    engine = create_async_engine(config.pravda.database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            attempt = (
                await session.execute(
                    select(db.Attempt)
                    .where(db.Attempt.id == uuid.UUID(attempt_id))
                    .options(
                        selectinload(db.Attempt.inspection),
                        selectinload(db.Attempt.candidate).selectinload(
                            db.Candidate.url
                        ),
                        selectinload(db.Attempt.candidate)
                        .selectinload(db.Candidate.subject)
                        .selectinload(db.Subject.dataset),
                    )
                )
            ).scalar_one_or_none()
            if attempt is None:
                raise LookupError(f"attempt {attempt_id} not found")
            if attempt.inspection is None:
                raise LookupError(f"attempt {attempt_id} has no Hit/Miss inspection")
            candidate = attempt.candidate
            url = candidate.url.url
            brief = Brief(
                people_sought=candidate.subject.dataset.people_sought,
                subject_label=candidate.subject.dataset.subject_label,
                subject=candidate.subject.name,
            )
            # End the read transaction before Pravda and LLM work;
            # expire_on_commit=False keeps the loaded attributes alive.
            await session.commit()

            pravda = pravda_client(config.pravda)
            async with pravda:
                snapshots = await pravda.snapshots(url)
            snapshot = next(
                (
                    snapshot
                    for snapshot in snapshots
                    if snapshot.id == attempt.snapshot_id
                ),
                None,
            )
            if snapshot is None:
                raise LookupError(f"snapshot {attempt.snapshot_id} not found for {url}")
            issue = inspectability_issue(snapshot)
            if issue is not None:
                raise ValueError(
                    f"usable attempt {attempt_id} has uninspectable snapshot: {issue}"
                )

            fs = artifact_filesystem(config.pravda)
            html = (await read_artifact(fs, snapshot.rendered_html)).decode("utf-8")
            urls = page_link_urls(snapshot.final_url, html)
            if not urls:
                log.info("%s → no page links, skipping discovery", url)
                return

            metadata = metadata_from_html(
                url,
                html,
                final_url=snapshot.final_url,
                http_status=snapshot.http_status,
                capture_error=first_error_line(snapshot.error),
            )
            outline = build_outline(snapshot.final_url, html, snapshot.http_archive)
            log.info("%s → discovering …", url)
            run = await discovery_agent.run(
                build_prompt(metadata, outline),
                model=model,
                run_id=attempt_id,
                deps=brief,
            )
            write_session(
                session_path(config.sessions.base_path, "discovery", attempt_id),
                run.all_messages_json(),
            )
            dropped = [
                selection
                for selection in run.output.selections
                if selection.url not in urls
            ]
            if dropped:
                log.warning(
                    "%s → dropping %d out-of-set link selection(s)", url, len(dropped)
                )
            selections = [
                selection
                for selection in run.output.selections
                if selection.url in urls
            ]

            inserted = await db.store_discovered_candidates(
                session,
                subject_id=candidate.subject_id,
                attempt_id=attempt.id,
                links=selections,
            )
            await session.commit()
            log.info("%s → discovered %d new candidate(s)", url, inserted)
    finally:
        await engine.dispose()


@app.task(name=Task.INSPECT_CANDIDATE, queue=Queue.INSPECT)
async def inspect_candidate(candidate_id: str) -> None:
    """Capture one Candidate's URL and judge it against its inspection brief.

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
                    selectinload(db.Candidate.subject).selectinload(db.Subject.dataset),
                    selectinload(db.Candidate.url),
                ),
            )
            if candidate is None:
                raise LookupError(f"candidate {candidate_id} not found")
            url = candidate.url.url
            people_sought = candidate.subject.dataset.people_sought
            subject_label = candidate.subject.dataset.subject_label
            subject = candidate.subject.name
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
                brief=Brief(
                    people_sought=people_sought,
                    subject_label=subject_label,
                    subject=subject,
                ),
                read_resource=partial(read_artifact, fs),
                resource_media_types=har_resource_media_types(snapshot.http_archive),
            )
            log.info("%s → extracting …", snapshot.final_url)
            run = await extraction_agent.run(
                build_prompt(metadata, outline),
                model=model,
                run_id=str(attempt_id),
                deps=deps,
            )
            session_file = session_path(
                config.sessions.base_path, "extraction", str(attempt_id)
            )

            if isinstance(run.output, BrokenSnapshot):
                reason = run.output.reason
                write_session(session_file, run.all_messages_json())
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

            write_session(session_file, run.all_messages_json())
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

            # The inspection must be durable before discovery is
            # deferred: discover_links reloads the attempt and its
            # inspection from the database.
            db.store_inspection(
                session,
                attempt_id=attempt_id,
                candidate_id=candidate.id,
                snapshot=snapshot,
                result=run.output,
                model=model,
            )
            await session.commit()
            await discover_links.defer_async(attempt_id=str(attempt_id))
    finally:
        await engine.dispose()
