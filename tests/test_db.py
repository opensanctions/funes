"""Unit tests for extraction outcome state-transition helpers."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from funes.db import Extraction, Person, extraction_broken, extraction_succeeded
from funes.extract import Extraction as ExtractionResult
from funes.extract import Person as PersonResult
from funes.extract import Position as PositionResult


class FakeSession:
    """Collects add_all calls so persisted graphs can be inspected."""

    def __init__(self) -> None:
        self.added: list[object] = []

    def add_all(self, objects) -> None:
        self.added.extend(objects)


def make_snapshot() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), captured_at=datetime.now(UTC))


def test_extraction_succeeded_sets_outcome_and_persons():
    extraction = Extraction(url="https://example.org", model="test-model")
    snapshot = make_snapshot()
    session = FakeSession()
    result = ExtractionResult(
        persons=[
            PersonResult(
                name="Jane Doe",
                countries=["Utopia"],
                positions=[PositionResult(name="Chair")],
            )
        ]
    )

    extraction_succeeded(session, extraction, snapshot, result)

    assert extraction.snapshot_id == snapshot.id
    assert extraction.captured_at == snapshot.captured_at
    assert extraction.outcome == "extracted"
    assert extraction.processed_at is not None
    assert extraction.broken_reason is None

    persons = [obj for obj in session.added if isinstance(obj, Person)]
    assert [p.name for p in persons] == ["Jane Doe"]
    assert [pos.name for pos in persons[0].positions] == ["Chair"]


def test_extraction_broken_sets_reason_without_persons():
    extraction = Extraction(url="https://example.org", model="test-model")
    snapshot = make_snapshot()
    session = FakeSession()

    extraction_broken(session, extraction, snapshot, "capture timed out")

    assert extraction.snapshot_id == snapshot.id
    assert extraction.captured_at == snapshot.captured_at
    assert extraction.outcome == "broken"
    assert extraction.processed_at is not None
    assert extraction.broken_reason == "capture timed out"
    assert session.added == []


def test_new_extraction_is_pending():
    extraction = Extraction(url="https://example.org", model="test-model")
    assert extraction.snapshot_id is None
    assert extraction.captured_at is None
    assert extraction.outcome is None
    assert extraction.processed_at is None
    assert extraction.broken_reason is None
