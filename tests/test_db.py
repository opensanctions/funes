"""Unit tests for inspection-ledger persistence."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from funes.db import (
    Base,
    Inspection,
    Outcome,
    Page,
    Person,
    Position,
    select_due_pages,
    store_inspection,
)
from funes.extract import Extraction as ExtractionResult
from funes.extract import Person as PersonResult
from funes.extract import Position as PositionResult


class FakeSession:
    """Collects add calls so persisted graphs can be inspected."""

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj) -> None:
        self.added.append(obj)


def make_snapshot() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), captured_at=datetime.now(UTC))


def make_result(persons: list[PersonResult] | None = None) -> ExtractionResult:
    return ExtractionResult(persons=persons or [])


def test_store_inspection_productive_persists_completed_graph():
    session = FakeSession()
    inspection_id = uuid4()
    page_id = uuid4()
    snapshot = make_snapshot()
    result = make_result(
        [
            PersonResult(
                name="Jane Doe",
                countries=["Utopia"],
                positions=[PositionResult(name="Chair", organization="Board")],
            )
        ]
    )

    inspection = store_inspection(
        session,
        inspection_id=inspection_id,
        page_id=page_id,
        outcome=Outcome.PRODUCTIVE,
        model="test-model",
        snapshot=snapshot,
        result=result,
    )

    assert isinstance(inspection, Inspection)
    assert inspection.id == inspection_id
    assert inspection.page_id == page_id
    assert inspection.outcome == Outcome.PRODUCTIVE
    assert inspection.reason is None
    assert inspection.model == "test-model"
    assert inspection.snapshot_id == snapshot.id
    assert inspection.captured_at == snapshot.captured_at
    assert inspection.extracted_at is not None
    assert session.added == [inspection]

    persons = inspection.persons
    assert [p.name for p in persons] == ["Jane Doe"]
    assert [p.inspection_id for p in persons] == [inspection_id]
    positions: list[Position] = persons[0].positions
    assert [pos.name for pos in positions] == ["Chair"]
    assert positions[0].organization == "Board"


def test_store_inspection_empty_outcome_has_no_graph():
    session = FakeSession()

    inspection = store_inspection(
        session,
        inspection_id=uuid4(),
        page_id=uuid4(),
        outcome=Outcome.EMPTY,
        model="test-model",
        snapshot=make_snapshot(),
        result=make_result(),
    )

    assert inspection.outcome == Outcome.EMPTY
    assert inspection.extracted_at is not None
    assert inspection.persons == []
    assert all(not isinstance(obj, Person) for obj in session.added)


def test_store_inspection_broken_at_capture():
    session = FakeSession()
    snapshot = make_snapshot()

    inspection = store_inspection(
        session,
        inspection_id=uuid4(),
        page_id=uuid4(),
        outcome=Outcome.BROKEN,
        snapshot=snapshot,
        reason="capture failed: net error",
    )

    assert inspection.outcome == Outcome.BROKEN
    assert inspection.reason == "capture failed: net error"
    assert inspection.model is None
    assert inspection.extracted_at is None
    assert inspection.snapshot_id == snapshot.id
    assert inspection.persons == []


def test_store_inspection_broken_by_model_verdict():
    session = FakeSession()

    inspection = store_inspection(
        session,
        inspection_id=uuid4(),
        page_id=uuid4(),
        outcome=Outcome.BROKEN,
        model="test-model",
        snapshot=make_snapshot(),
        reason="model: page is a PDF index with no content",
    )

    assert inspection.outcome == Outcome.BROKEN
    assert inspection.model == "test-model"
    assert inspection.extracted_at is None
    assert inspection.reason is not None


@pytest.mark.parametrize(
    ("kwargs", "outcome"),
    [
        # productive/empty: model, result required; reason forbidden.
        ({"model": None, "result": make_result()}, Outcome.PRODUCTIVE),
        ({"model": "m", "result": None}, Outcome.PRODUCTIVE),
        ({"model": "m", "result": make_result(), "reason": "why"}, Outcome.PRODUCTIVE),
        ({"model": None, "result": make_result()}, Outcome.EMPTY),
        ({"model": "m", "result": None}, Outcome.EMPTY),
        # broken: reason required; result forbidden.
        ({}, Outcome.BROKEN),
        ({"reason": "r", "result": make_result()}, Outcome.BROKEN),
    ],
)
def test_store_inspection_rejects_inconsistent_combinations(kwargs, outcome):
    session = FakeSession()
    with pytest.raises(ValueError):
        store_inspection(
            session,
            inspection_id=uuid4(),
            page_id=uuid4(),
            snapshot=make_snapshot(),
            outcome=outcome,
            **kwargs,
        )


# --- select_due_pages: real queries against an in-memory async SQLite DB ---


def make_page(url: str) -> Page:
    return Page(id=uuid4(), url=url, created_at=datetime.now(UTC))


def make_inspection(page: Page, outcome: Outcome, created_at: datetime) -> Inspection:
    return Inspection(
        id=uuid4(),
        page_id=page.id,
        snapshot_id=uuid4(),
        outcome=outcome,
        model="m",
        created_at=created_at,
        captured_at=created_at - timedelta(minutes=1),
    )


@pytest.mark.parametrize(
    ("outcome", "age", "due"),
    [
        (None, None, True),  # never attempted
        (Outcome.PRODUCTIVE, timedelta(days=30), True),  # productive and old
        (Outcome.PRODUCTIVE, timedelta(hours=1), False),  # productive and recent
        (Outcome.EMPTY, timedelta(days=30), False),  # empty verdict sticks
        (Outcome.BROKEN, timedelta(days=30), False),  # broken verdict sticks
    ],
)
def test_select_due_pages(outcome, age, due):
    def run() -> list[str]:
        async def scenario(session: AsyncSession) -> list[str]:
            page = make_page("https://example.com/a")
            session.add(page)
            if outcome is not None:
                session.add(make_inspection(page, outcome, datetime.now(UTC) - age))
            await session.commit()
            due_pages = await select_due_pages(session, timedelta(days=7))
            return [p.url for p in due_pages]

        return asyncio.run(run_with_session(scenario))

    assert (run() == ["https://example.com/a"]) is due


def test_select_due_pages_orders_by_url():
    def run() -> list[str]:
        async def scenario(session: AsyncSession) -> list[str]:
            for url in (
                "https://example.com/c",
                "https://example.com/a",
                "https://example.com/b",
            ):
                session.add(Page(id=uuid4(), url=url, created_at=datetime.now(UTC)))
            await session.commit()
            due_pages = await select_due_pages(session, timedelta(days=7))
            return [p.url for p in due_pages]

        return asyncio.run(run_with_session(scenario))

    assert run() == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]


def test_select_due_pages_uses_latest_inspection():
    """A recent empty verdict overrides an old productive one: not due."""

    def run() -> list[Page]:
        async def scenario(session: AsyncSession) -> list[Page]:
            page = make_page("https://example.com/a")
            session.add(page)
            session.add(
                make_inspection(
                    page, Outcome.PRODUCTIVE, datetime.now(UTC) - timedelta(days=30)
                )
            )
            session.add(
                make_inspection(
                    page, Outcome.EMPTY, datetime.now(UTC) - timedelta(days=1)
                )
            )
            await session.commit()
            return await select_due_pages(session, timedelta(days=7))

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
