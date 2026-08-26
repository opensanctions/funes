"""Tests for extraction outcome persistence in funes.db."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from funes.db import (
    Base,
    Extraction,
    Person,
    extraction_broken,
    extraction_succeeded,
    register_extractions,
)
from funes.extract import Extraction as ExtractionResult
from funes.extract import Person as PersonResult
from funes.extract import Position as PositionResult


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as sess:
        yield sess
    await engine.dispose()


def make_snapshot() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), captured_at=datetime.now(UTC))


async def make_extraction(sess) -> Extraction:
    extractions = await register_extractions(
        sess, [("ds", "https://example.org", "org")], "test-model"
    )
    await sess.flush()
    return extractions["https://example.org"]


async def fetch_fresh(sess, extraction_id) -> Extraction:
    sess.expire_all()
    return await sess.get(Extraction, extraction_id)


@pytest.mark.asyncio
async def test_pending_extraction_has_null_outcome(session):
    extraction = await make_extraction(session)
    assert extraction.outcome is None
    assert extraction.processed_at is None
    assert extraction.broken_reason is None
    await session.commit()


@pytest.mark.asyncio
async def test_extraction_succeeded_sets_outcome_and_persons(session):
    extraction = await make_extraction(session)
    result = ExtractionResult(
        persons=[
            PersonResult(
                name="Jane Doe",
                countries=["Utopia"],
                positions=[PositionResult(name="Chair")],
            )
        ]
    )
    extraction_succeeded(session, extraction, make_snapshot(), result)
    await session.commit()

    fresh = await fetch_fresh(session, extraction.id)
    assert fresh.outcome == "extracted"
    assert fresh.processed_at is not None
    assert fresh.broken_reason is None
    persons = (await session.execute(select(Person))).scalars().all()
    assert [p.name for p in persons] == ["Jane Doe"]


@pytest.mark.asyncio
async def test_extraction_broken_sets_reason_without_persons(session):
    extraction = await make_extraction(session)
    extraction_broken(session, extraction, make_snapshot(), "capture timed out")
    await session.commit()

    fresh = await fetch_fresh(session, extraction.id)
    assert fresh.outcome == "broken"
    assert fresh.processed_at is not None
    assert fresh.snapshot_id is not None
    assert fresh.captured_at is not None
    assert fresh.broken_reason == "capture timed out"
    assert (await session.execute(select(Person))).scalars().all() == []


@pytest.mark.asyncio
async def test_partial_state_violates_constraint(session):
    from sqlalchemy.exc import IntegrityError

    extraction = await make_extraction(session)
    extraction.snapshot_id = uuid4()  # snapshot without outcome/processed_at
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_extracted_with_reason_violates_constraint(session):
    from sqlalchemy.exc import IntegrityError

    extraction = await make_extraction(session)
    extraction_succeeded(
        session, extraction, make_snapshot(), ExtractionResult(persons=[])
    )
    extraction.broken_reason = "contradiction"
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_broken_without_reason_violates_constraint(session):
    from sqlalchemy.exc import IntegrityError

    extraction = await make_extraction(session)
    extraction_broken(session, extraction, make_snapshot(), "reason")
    extraction.broken_reason = None
    with pytest.raises(IntegrityError):
        await session.flush()
