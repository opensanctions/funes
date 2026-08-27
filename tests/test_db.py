"""Behavior tests for the candidate/attempt persistence backbone."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from funes.db import (
    Attempt,
    Base,
    Candidate,
    Dataset,
    Inspection,
    InspectionOutcome,
    Objective,
    Person,
    Position,
    SnapshotAssessment,
    SnapshotStatus,
    Url,
    import_candidates,
    select_candidates,
    select_due_candidates,
    store_broken_attempt,
    store_inspection,
)
from funes.extract import BrokenSnapshot, Hit, Miss
from funes.extract import Person as PersonResult
from funes.extract import Position as PositionResult


class FakeSession:
    """Collects add calls so persisted aggregates can be inspected."""

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj) -> None:
        self.added.append(obj)


def make_snapshot() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), captured_at=datetime.now(UTC))


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


# --- import_candidates: append-only catalogue import against in-memory SQLite ---


def test_import_candidates_creates_full_catalogue():
    """One objective with several URLs, and one URL under two objectives."""

    def run() -> dict[str, list]:
        async def scenario(session: AsyncSession) -> dict[str, list]:
            await import_candidates(
                session,
                [
                    ("one", "find board members", "https://a.example"),
                    ("one", "find board members", "https://b.example"),
                    ("two", "find legislators", "https://a.example"),
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
                    select(Dataset.name, Objective.description, Url.url)
                    .join(Objective, Objective.dataset_id == Dataset.id)
                    .join(Candidate, Candidate.objective_id == Objective.id)
                    .join(Url, Url.id == Candidate.url_id)
                    .order_by(Dataset.name, Objective.description, Url.url)
                )
            ).all()
            objective_counts = (
                (
                    await session.execute(
                        select(func.count())
                        .select_from(Objective)
                        .group_by(Objective.dataset_id)
                        .order_by(func.count())
                    )
                )
                .scalars()
                .all()
            )
            return {
                "datasets": list(datasets),
                "rows": rows,
                "objective_counts": list(objective_counts),
            }

        return asyncio.run(run_with_session(scenario))

    assert run() == {
        "datasets": ["one", "two"],
        # One url row shared by both objectives; one candidate per pairing.
        "rows": [
            ("one", "find board members", "https://a.example"),
            ("one", "find board members", "https://b.example"),
            ("two", "find legislators", "https://a.example"),
        ],
        # Dataset one owns one objective covering both urls; dataset two owns one.
        "objective_counts": [1, 1],
    }


def test_import_candidates_is_idempotent():
    """Re-importing identical rows changes nothing."""

    def run() -> tuple[int, int, int, int]:
        rows = [
            ("one", "find board members", "https://a.example"),
            ("two", "find legislators", "https://a.example"),
        ]

        async def scenario(session: AsyncSession) -> tuple[int, int, int, int]:
            from sqlalchemy import func, select

            async def count(table) -> int:
                return (
                    await session.execute(select(func.count()).select_from(table))
                ).scalar_one()

            await import_candidates(session, rows)
            await session.commit()
            await import_candidates(session, rows)
            await session.commit()
            return (
                await count(Objective),
                await count(Url),
                await count(Candidate),
                await count(Dataset),
            )

        return asyncio.run(run_with_session(scenario))

    assert run() == (2, 1, 2, 2)


def test_select_candidates_orders_deterministically():
    def run() -> list[tuple[str, str, str]]:
        async def scenario(session: AsyncSession) -> list[tuple[str, str, str]]:
            await import_candidates(
                session,
                [
                    ("zeta", "last objective", "https://z.example"),
                    ("alpha", "find board members", "https://m.example"),
                    ("alpha", "find legislators", "https://a.example"),
                    ("alpha", "find board members", "https://a.example"),
                ],
            )
            await session.commit()
            return [
                (c.objective.dataset.name, c.objective.description, c.url.url)
                for c in await select_candidates(session)
            ]

        return asyncio.run(run_with_session(scenario))

    assert run() == [
        ("alpha", "find board members", "https://a.example"),
        ("alpha", "find board members", "https://m.example"),
        ("alpha", "find legislators", "https://a.example"),
        ("zeta", "last objective", "https://z.example"),
    ]


# --- terminal constructors: aggregate construction against a collecting session ---


def test_store_broken_attempt_deterministic():
    session = FakeSession()
    candidate_id = uuid4()
    snapshot = make_snapshot()

    attempt = store_broken_attempt(
        session,
        candidate_id=candidate_id,
        snapshot=snapshot,
        reason="capture failed: net error",
    )

    assert isinstance(attempt, Attempt)
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
    assert assessment.assessed_at is not None


def test_store_broken_attempt_model_detected():
    session = FakeSession()

    attempt = store_broken_attempt(
        session,
        candidate_id=uuid4(),
        snapshot=make_snapshot(),
        reason="Cloudflare challenge page",
        model="openai:gpt-test",
    )

    assert attempt.assessment.model == "openai:gpt-test"
    assert attempt.assessment.status == SnapshotStatus.BROKEN
    assert attempt.inspection is None


def test_store_broken_attempt_requires_reason():
    session = FakeSession()
    with pytest.raises(ValueError):
        store_broken_attempt(
            session, candidate_id=uuid4(), snapshot=make_snapshot(), reason="  "
        )
    assert session.added == []


def test_store_inspection_hit_maps_person_graph():
    session = FakeSession()
    candidate_id = uuid4()
    snapshot = make_snapshot()

    inspection = store_inspection(
        session,
        candidate_id=candidate_id,
        snapshot=snapshot,
        result=make_hit(),
        model="openai:gpt-test",
    )

    assert isinstance(inspection, Inspection)
    assert session.added == [inspection.attempt]
    attempt = inspection.attempt
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
    assert inspection.created_at is not None

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


def test_store_inspection_requires_model():
    session = FakeSession()
    with pytest.raises(ValueError):
        store_inspection(
            session,
            candidate_id=uuid4(),
            snapshot=make_snapshot(),
            result=Miss(reason="nothing here"),
            model=" ",
        )
    assert session.added == []


def test_store_inspection_rejects_broken_and_unknown_results():
    session = FakeSession()
    with pytest.raises(ValueError):
        store_inspection(
            session,
            candidate_id=uuid4(),
            snapshot=make_snapshot(),
            result=BrokenSnapshot(reason="challenge page"),
            model="openai:gpt-test",
        )
    with pytest.raises(ValueError):
        store_inspection(
            session,
            candidate_id=uuid4(),
            snapshot=make_snapshot(),
            result=object(),  # type: ignore[arg-type]
            model="openai:gpt-test",
        )
    assert session.added == []


# --- select_due_candidates: real queries against in-memory async SQLite ---


async def add_candidate(
    session: AsyncSession, dataset: str, objective: str, url: str
) -> Candidate:
    await import_candidates(session, [(dataset, objective, url)])
    await session.flush()
    stmt = (
        select(Candidate)
        .join(Objective, Objective.id == Candidate.objective_id)
        .join(Dataset, Dataset.id == Objective.dataset_id)
        .join(Url, Url.id == Candidate.url_id)
        .where(
            (Dataset.name == dataset)
            & (Objective.description == objective)
            & (Url.url == url)
        )
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
                session, "one", "find board members", "https://a.example"
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
                (c.objective.dataset.name, c.objective.description, c.url.url)
                for c in await select_due_candidates(session, timedelta(days=7))
            ]

        return asyncio.run(run_with_session(scenario))

    expected = [("one", "find board members", "https://a.example")]
    assert (run() == expected) is due


def test_select_due_candidates_recent_result_supersedes_older_hit():
    """A recent miss over an old hit makes the candidate not due."""

    def run() -> list[tuple[str, str, str]]:
        async def scenario(session: AsyncSession) -> list[tuple[str, str, str]]:
            candidate = await add_candidate(
                session, "one", "find board members", "https://a.example"
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
                (c.objective.dataset.name, c.objective.description, c.url.url)
                for c in await select_due_candidates(session, timedelta(days=7))
            ]

        return asyncio.run(run_with_session(scenario))

    assert run() == []


def test_select_due_candidates_orders_deterministically():
    def run() -> list[str]:
        async def scenario(session: AsyncSession) -> list[str]:
            await import_candidates(
                session,
                [
                    ("two", "find legislators", "https://a.example"),
                    ("one", "find legislators", "https://b.example"),
                    ("one", "find board members", "https://c.example"),
                ],
            )
            await session.commit()
            return [
                c.url.url
                for c in await select_due_candidates(session, timedelta(days=7))
            ]

        return asyncio.run(run_with_session(scenario))

    # Dataset name, then objective description, then url.
    assert run() == ["https://c.example", "https://b.example", "https://a.example"]


def test_select_due_candidates_breaks_created_at_ties_by_attempt_id():
    """Equal created_at: the greater attempt id is the latest verdict."""

    def run() -> list[str]:
        async def scenario(session: AsyncSession) -> list[str]:
            candidate = await add_candidate(
                session, "one", "find board members", "https://a.example"
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
