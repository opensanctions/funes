"""Behavior tests for the candidate/attempt persistence backbone."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from funes.db import (
    Attempt,
    Base,
    Candidate,
    Dataset,
    Inspection,
    InspectionOutcome,
    Person,
    Position,
    SnapshotAssessment,
    SnapshotStatus,
    Subject,
    Url,
    import_catalogue,
    select_due_candidates,
    store_broken_attempt,
    store_discovered_candidates,
    store_inspection,
)
from funes.extract import Hit, LinkSelection, Miss
from funes.extract import Person as PersonResult
from funes.extract import Position as PositionResult
from funes.sources import DatasetDefinition, SubjectDefinition


class FakeSession:
    """Collects add calls so persisted aggregates can be inspected."""

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj) -> None:
        self.added.append(obj)


def make_snapshot() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), captured_at=datetime.now(UTC))


def make_definition(
    name: str,
    people_sought: str,
    subject_label: str,
    subjects: dict[str, list[str]],
) -> DatasetDefinition:
    return DatasetDefinition(
        name=name,
        people_sought=people_sought,
        subject_label=subject_label,
        subjects=[
            SubjectDefinition(name=subject, urls=urls)
            for subject, urls in subjects.items()
        ],
    )


def make_hit() -> Hit:
    return Hit(
        persons=[
            PersonResult(
                name="Jane Doe",
                countries=["Utopia"],
                positions=[
                    PositionResult(name="Chair", organization="Board"),
                    PositionResult(name="Trustee", organization="Fund"),
                ],
            ),
            PersonResult(
                name="John Roe",
                positions=[PositionResult(name="Director")],
            ),
        ]
    )


# --- import_catalogue: append-only catalogue import against in-memory SQLite ---


def test_import_catalogue_creates_full_catalogue():
    """One subject with several URLs, and one URL under two subjects."""

    def run() -> dict[str, list]:
        async def scenario(session: AsyncSession) -> dict[str, list]:
            await import_catalogue(
                session,
                [
                    make_definition(
                        "one",
                        "Board members",
                        "Organization",
                        {
                            "Example Foundation": [
                                "https://a.example",
                                "https://b.example",
                            ]
                        },
                    ),
                    make_definition(
                        "two",
                        "Legislators",
                        "Sending country",
                        {"France": ["https://a.example"]},
                    ),
                ],
            )
            await session.commit()
            datasets = (
                (await session.execute(select(Dataset.name).order_by(Dataset.name)))
                .scalars()
                .all()
            )
            rows = (
                await session.execute(
                    select(Dataset.name, Subject.name, Url.url)
                    .join(Subject, Subject.dataset_id == Dataset.id)
                    .join(Candidate, Candidate.subject_id == Subject.id)
                    .join(Url, Url.id == Candidate.url_id)
                    .order_by(Dataset.name, Subject.name, Url.url)
                )
            ).all()
            subject_counts = (
                (
                    await session.execute(
                        select(func.count())
                        .select_from(Subject)
                        .group_by(Subject.dataset_id)
                        .order_by(func.count())
                    )
                )
                .scalars()
                .all()
            )
            return {
                "datasets": list(datasets),
                "rows": rows,
                "subject_counts": list(subject_counts),
            }

        return asyncio.run(run_with_session(scenario))

    assert run() == {
        "datasets": ["one", "two"],
        # One url row shared by both subjects; one candidate per pairing.
        "rows": [
            ("one", "Example Foundation", "https://a.example"),
            ("one", "Example Foundation", "https://b.example"),
            ("two", "France", "https://a.example"),
        ],
        # Dataset one owns one subject covering both urls; dataset two owns one.
        "subject_counts": [1, 1],
    }


def test_import_catalogue_is_idempotent():
    """Re-importing identical definitions changes nothing."""

    def run() -> tuple[int, int, int, int]:
        definitions = [
            make_definition(
                "one",
                "Board members",
                "Organization",
                {"Example Foundation": ["https://a.example"]},
            ),
            make_definition(
                "two",
                "Legislators",
                "Sending country",
                {"France": ["https://a.example"]},
            ),
        ]

        async def scenario(session: AsyncSession) -> tuple[int, int, int, int]:
            async def count(table) -> int:
                return (
                    await session.execute(select(func.count()).select_from(table))
                ).scalar_one()

            await import_catalogue(session, definitions)
            await session.commit()
            await import_catalogue(session, definitions)
            await session.commit()
            return (
                await count(Subject),
                await count(Url),
                await count(Candidate),
                await count(Dataset),
            )

        return asyncio.run(run_with_session(scenario))

    assert run() == (2, 1, 2, 2)


def test_import_catalogue_allows_subjects_without_seed_urls():
    async def scenario(session: AsyncSession) -> tuple[int, int, int]:
        await import_catalogue(
            session,
            [
                make_definition(
                    "courts", "Judges", "Court", {"High Court of Australia": []}
                )
            ],
        )
        await session.commit()
        return (
            (
                await session.execute(select(func.count()).select_from(Subject))
            ).scalar_one(),
            (await session.execute(select(func.count()).select_from(Url))).scalar_one(),
            (
                await session.execute(select(func.count()).select_from(Candidate))
            ).scalar_one(),
        )

    assert asyncio.run(run_with_session(scenario)) == (1, 0, 0)


def test_import_catalogue_syncs_dataset_brief():
    async def scenario(session: AsyncSession) -> str:
        configured = make_definition("one", "Heads", "Organization", {})
        await import_catalogue(session, [configured])
        await session.commit()
        changed = configured.model_copy(update={"people_sought": "Board members"})
        await import_catalogue(session, [changed])
        await session.commit()
        return (
            await session.execute(
                select(Dataset.people_sought).where(Dataset.name == "one")
            )
        ).scalar_one()

    assert asyncio.run(run_with_session(scenario)) == "Board members"


# --- terminal constructors: aggregate construction against a collecting session ---


def test_store_broken_attempt_deterministic():
    session = FakeSession()
    candidate_id = uuid4()
    snapshot = make_snapshot()

    attempt_id = uuid4()
    attempt = store_broken_attempt(
        session,
        attempt_id=attempt_id,
        candidate_id=candidate_id,
        snapshot=snapshot,
        reason="capture failed: net error",
    )

    assert isinstance(attempt, Attempt)
    assert attempt.id == attempt_id
    assert session.added == [attempt]
    assert attempt.candidate_id == candidate_id
    assert attempt.snapshot_id == snapshot.id
    assert attempt.captured_at == snapshot.captured_at
    assert attempt.inspection is None

    assessment = attempt.assessment
    assert isinstance(assessment, SnapshotAssessment)
    assert assessment.snapshot_id == snapshot.id
    assert assessment.status == SnapshotStatus.BROKEN
    assert assessment.reason == "capture failed: net error"
    assert assessment.model is None


def test_store_broken_attempt_model_detected():
    session = FakeSession()

    attempt = store_broken_attempt(
        session,
        attempt_id=uuid4(),
        candidate_id=uuid4(),
        snapshot=make_snapshot(),
        reason="Cloudflare challenge page",
        model="openai:gpt-test",
    )

    assert attempt.assessment.model == "openai:gpt-test"
    assert attempt.assessment.status == SnapshotStatus.BROKEN
    assert attempt.inspection is None


def test_stored_aggregates_navigate_from_assessment_to_candidate():
    """Repair code loads an assessment and walks to its attempt and candidate."""

    def run() -> tuple[str, str, str, str]:
        async def scenario(session: AsyncSession) -> tuple[str, str, str, str]:
            candidate = await add_candidate(
                session, "one", "Example Foundation", "https://a.example"
            )
            attempt = store_broken_attempt(
                session,
                attempt_id=uuid4(),
                candidate_id=candidate.id,
                snapshot=make_snapshot(),
                reason="Cloudflare challenge",
                model="openai:gpt-test",
            )
            await session.commit()
            loaded = (
                await session.execute(
                    select(SnapshotAssessment)
                    .options(
                        selectinload(SnapshotAssessment.attempt)
                        .selectinload(Attempt.candidate)
                        .selectinload(Candidate.url),
                        selectinload(SnapshotAssessment.attempt)
                        .selectinload(Attempt.candidate)
                        .selectinload(Candidate.subject),
                    )
                    .where(SnapshotAssessment.snapshot_id == attempt.snapshot_id)
                )
            ).scalar_one()
            return (
                loaded.attempt.id == attempt.id and "attempt",
                loaded.attempt.candidate.url.url,
                loaded.attempt.candidate.subject.name,
                loaded.reason or "",
                loaded.status,
                loaded.attempt.created_at is not None
                and loaded.assessed_at is not None
                and "server defaults",
            )

        return asyncio.run(run_with_session(scenario))

    assert run() == (
        "attempt",
        "https://a.example",
        "Example Foundation",
        "Cloudflare challenge",
        SnapshotStatus.BROKEN,
        "server defaults",
    )


def test_store_inspection_hit_maps_person_graph():
    session = FakeSession()
    candidate_id = uuid4()
    snapshot = make_snapshot()

    attempt_id = uuid4()
    inspection = store_inspection(
        session,
        attempt_id=attempt_id,
        candidate_id=candidate_id,
        snapshot=snapshot,
        result=make_hit(),
        model="openai:gpt-test",
    )

    assert isinstance(inspection, Inspection)
    assert session.added == [inspection.attempt]
    attempt = inspection.attempt
    assert attempt.id == attempt_id
    assert attempt.candidate_id == candidate_id
    assert attempt.snapshot_id == snapshot.id
    assert attempt.captured_at == snapshot.captured_at

    assessment = attempt.assessment
    assert assessment.status == SnapshotStatus.USABLE
    assert assessment.reason is None
    assert assessment.model == "openai:gpt-test"

    assert inspection.outcome == InspectionOutcome.HIT
    assert inspection.reason is None
    assert inspection.model == "openai:gpt-test"

    assert [p.name for p in inspection.persons] == ["Jane Doe", "John Roe"]
    jane = inspection.persons[0]
    assert jane.countries == ["Utopia"]
    assert [(pos.name, pos.organization) for pos in jane.positions] == [
        ("Chair", "Board"),
        ("Trustee", "Fund"),
    ]
    assert all(pos.person is jane for pos in jane.positions)


def test_store_inspection_miss_has_reason_and_no_graph():
    session = FakeSession()

    inspection = store_inspection(
        session,
        attempt_id=uuid4(),
        candidate_id=uuid4(),
        snapshot=make_snapshot(),
        result=Miss(reason="page lists press releases only"),
        model="openai:gpt-test",
    )

    assert inspection.outcome == InspectionOutcome.MISS
    assert inspection.reason == "page lists press releases only"
    assert inspection.persons == []
    assert inspection.attempt.assessment.status == SnapshotStatus.USABLE
    assert inspection.attempt.assessment.reason is None


# --- select_due_candidates: real queries against in-memory async SQLite ---


async def add_candidate(
    session: AsyncSession, dataset: str, subject: str, url: str
) -> Candidate:
    await import_catalogue(
        session,
        [make_definition(dataset, "Board members", "Organization", {subject: [url]})],
    )
    await session.flush()
    stmt = (
        select(Candidate)
        .join(Subject, Subject.id == Candidate.subject_id)
        .join(Dataset, Dataset.id == Subject.dataset_id)
        .join(Url, Url.id == Candidate.url_id)
        .where((Dataset.name == dataset) & (Subject.name == subject) & (Url.url == url))
    )
    return (await session.execute(stmt)).scalar_one()


def make_attempt(
    candidate: Candidate,
    created_at: datetime,
    *,
    outcome: InspectionOutcome | None = None,
    status: SnapshotStatus = SnapshotStatus.USABLE,
    attempt_id: UUID | None = None,
) -> Attempt:
    snapshot_id = uuid4()
    attempt = Attempt(
        id=attempt_id or uuid4(),
        candidate_id=candidate.id,
        snapshot_id=snapshot_id,
        captured_at=created_at - timedelta(minutes=1),
        created_at=created_at,
    )
    attempt.assessment = SnapshotAssessment(
        snapshot_id=snapshot_id,
        status=status,
        reason="unusable" if status is SnapshotStatus.BROKEN else None,
        model="openai:gpt-test",
        assessed_at=created_at,
    )
    if outcome is not None:
        attempt.inspection = Inspection(
            outcome=outcome,
            reason="miss reason" if outcome is InspectionOutcome.MISS else None,
            model="openai:gpt-test",
            created_at=created_at,
            persons=(
                [
                    Person(
                        name="Jane Doe",
                        countries=[],
                        positions=[Position(name="Chair")],
                    )
                ]
                if outcome is InspectionOutcome.HIT
                else []
            ),
        )
    return attempt


@pytest.mark.parametrize(
    ("verdict", "age", "due"),
    [
        (None, None, True),  # never attempted
        ("hit", timedelta(days=30), True),  # old hit is revisitable
        ("hit", timedelta(hours=1), False),  # recent hit blocks
        ("miss", timedelta(days=30), False),  # miss verdict sticks
        ("broken", timedelta(days=30), False),  # broken blocks normal queue
    ],
)
def test_select_due_candidates_by_latest_verdict(verdict, age, due):
    def run() -> list[tuple[str, str, str]]:
        async def scenario(session: AsyncSession) -> list[tuple[str, str, str]]:
            candidate = await add_candidate(
                session, "one", "Example Foundation", "https://a.example"
            )
            if verdict is not None:
                match verdict:
                    case "hit":
                        outcome, status = (
                            InspectionOutcome.HIT,
                            SnapshotStatus.USABLE,
                        )
                    case "miss":
                        outcome, status = (
                            InspectionOutcome.MISS,
                            SnapshotStatus.USABLE,
                        )
                    case "broken":
                        outcome, status = (None, SnapshotStatus.BROKEN)
                session.add(
                    make_attempt(
                        candidate,
                        datetime.now(UTC) - age,
                        outcome=outcome,
                        status=status,
                    )
                )
            await session.commit()
            return [
                (c.subject.dataset.name, c.subject.name, c.url.url)
                for c in await select_due_candidates(session, timedelta(days=7))
            ]

        return asyncio.run(run_with_session(scenario))

    expected = [("one", "Example Foundation", "https://a.example")]
    assert (run() == expected) is due


def test_select_due_candidates_recent_result_supersedes_older_hit():
    """A recent miss over an old hit makes the candidate not due."""

    def run() -> list[tuple[str, str, str]]:
        async def scenario(session: AsyncSession) -> list[tuple[str, str, str]]:
            candidate = await add_candidate(
                session, "one", "Example Foundation", "https://a.example"
            )
            session.add(
                make_attempt(
                    candidate,
                    datetime.now(UTC) - timedelta(days=30),
                    outcome=InspectionOutcome.HIT,
                )
            )
            session.add(
                make_attempt(
                    candidate,
                    datetime.now(UTC) - timedelta(days=1),
                    outcome=InspectionOutcome.MISS,
                )
            )
            await session.commit()
            return [
                (c.subject.dataset.name, c.subject.name, c.url.url)
                for c in await select_due_candidates(session, timedelta(days=7))
            ]

        return asyncio.run(run_with_session(scenario))

    assert run() == []


def test_select_due_candidates_orders_deterministically():
    def run() -> list[str]:
        async def scenario(session: AsyncSession) -> list[str]:
            await import_catalogue(
                session,
                [
                    make_definition(
                        "two",
                        "Legislators",
                        "Sending country",
                        {"France": ["https://a.example"]},
                    ),
                    make_definition(
                        "one",
                        "People",
                        "Subject",
                        {
                            "France": ["https://b.example"],
                            "Example Foundation": ["https://c.example"],
                        },
                    ),
                ],
            )
            await session.commit()
            return [
                c.url.url
                for c in await select_due_candidates(session, timedelta(days=7))
            ]

        return asyncio.run(run_with_session(scenario))

    # Dataset name, then subject name, then url.
    assert run() == ["https://c.example", "https://b.example", "https://a.example"]


def test_select_due_candidates_breaks_created_at_ties_by_attempt_id():
    """Equal created_at: the greater attempt id is the latest verdict."""

    def run() -> list[str]:
        async def scenario(session: AsyncSession) -> list[str]:
            candidate = await add_candidate(
                session, "one", "Example Foundation", "https://a.example"
            )
            tie = datetime.now(UTC) - timedelta(days=30)
            low, high = uuid4(), uuid4()
            if low > high:
                low, high = high, low
            session.add(
                make_attempt(
                    candidate, tie, attempt_id=high, outcome=InspectionOutcome.MISS
                )
            )
            session.add(
                make_attempt(
                    candidate, tie, attempt_id=low, outcome=InspectionOutcome.HIT
                )
            )
            await session.commit()
            return [
                c.url.url
                for c in await select_due_candidates(session, timedelta(days=7))
            ]

        # The miss (greater id) is the latest attempt: not due.
        return asyncio.run(run_with_session(scenario))

    assert run() == []


async def run_with_session[T](runner: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Run ``runner`` against a fresh in-memory async SQLite database."""
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            return await runner(session)
    finally:
        await engine.dispose()


# --- store_discovered_candidates: discovery provenance against in-memory SQLite ---


async def add_subject(session: AsyncSession, dataset: str, subject: str) -> UUID:
    await import_catalogue(
        session,
        [make_definition(dataset, "Board members", "Organization", {subject: []})],
    )
    await session.flush()
    stmt = select(Subject.id).where(Subject.name == subject)
    return (await session.execute(stmt)).scalar_one()


def make_links(*urls: str) -> list[LinkSelection]:
    return [LinkSelection(url=url, reason=f"{url} looks promising") for url in urls]


def test_store_discovered_candidates_records_provenance():
    def run() -> tuple[int, list[tuple[str, UUID, str]]]:
        async def scenario(
            session: AsyncSession,
        ) -> tuple[int, list[tuple[str, UUID, str]]]:
            subject_id = await add_subject(session, "one", "Example Foundation")
            attempt_id = uuid4()
            inserted = await store_discovered_candidates(
                session,
                subject_id=subject_id,
                attempt_id=attempt_id,
                links=make_links("https://a.example", "https://b.example"),
            )
            await session.commit()
            rows = (
                await session.execute(
                    select(Url.url, Candidate.attempt_id, Candidate.reason)
                    .join(Candidate, Candidate.url_id == Url.id)
                    .where(Candidate.subject_id == subject_id)
                    .order_by(Url.url)
                )
            ).all()
            return inserted, [(url, aid, reason) for url, aid, reason in rows]

        return asyncio.run(run_with_session(scenario))

    inserted, rows = run()
    assert inserted == 2
    assert [url for url, _, reason in rows] == [
        "https://a.example",
        "https://b.example",
    ]
    assert all(reason == f"{url} looks promising" for url, _, reason in rows)
    assert len({attempt_id for _, attempt_id, _ in rows}) == 1


def test_store_discovered_candidates_rediscovery_is_noop():
    """Re-discovering the same links keeps the original provenance."""

    first_attempt = uuid4()

    def run() -> tuple[int, UUID, str]:
        async def scenario(session: AsyncSession) -> tuple[int, UUID, str]:
            subject_id = await add_subject(session, "one", "Example Foundation")
            await store_discovered_candidates(
                session,
                subject_id=subject_id,
                attempt_id=first_attempt,
                links=make_links("https://a.example"),
            )
            await session.commit()
            rediscovered = await store_discovered_candidates(
                session,
                subject_id=subject_id,
                attempt_id=uuid4(),
                links=make_links("https://a.example"),
            )
            await session.commit()
            row = (
                await session.execute(
                    select(Candidate.attempt_id, Candidate.reason)
                    .join(Url, Url.id == Candidate.url_id)
                    .where(Candidate.subject_id == subject_id)
                )
            ).one()
            return rediscovered, row[0], row[1]

        return asyncio.run(run_with_session(scenario))

    rediscovered, attempt_id, reason = run()
    assert rediscovered == 0
    assert attempt_id == first_attempt
    assert reason == "https://a.example looks promising"


def test_store_discovered_candidates_preserves_catalogue_candidate():
    """A candidate seeded by the catalogue stays catalogue-sourced (null provenance)."""

    def run() -> tuple[int, UUID | None, str | None]:
        async def scenario(
            session: AsyncSession,
        ) -> tuple[int, UUID | None, str | None]:
            candidate = await add_candidate(
                session, "one", "Example Foundation", "https://a.example"
            )
            await session.commit()
            inserted = await store_discovered_candidates(
                session,
                subject_id=candidate.subject_id,
                attempt_id=uuid4(),
                links=make_links("https://a.example"),
            )
            await session.commit()
            row = (
                await session.execute(
                    select(Candidate.attempt_id, Candidate.reason).where(
                        Candidate.id == candidate.id
                    )
                )
            ).one()
            return inserted, row[0], row[1]

        return asyncio.run(run_with_session(scenario))

    assert run() == (0, None, None)


def test_store_discovered_candidates_empty_links_is_noop():
    def run() -> tuple[int, int, int]:
        async def scenario(session: AsyncSession) -> tuple[int, int, int]:
            subject_id = await add_subject(session, "one", "Example Foundation")
            inserted = await store_discovered_candidates(
                session, subject_id=subject_id, attempt_id=uuid4(), links=[]
            )
            await session.commit()

            async def count(table) -> int:
                return (
                    await session.execute(select(func.count()).select_from(table))
                ).scalar_one()

            return inserted, await count(Url), await count(Candidate)

        return asyncio.run(run_with_session(scenario))

    assert run() == (0, 0, 0)
