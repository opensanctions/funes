"""Unit tests for completed-extraction persistence."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from funes.db import Extraction, Person, Position, store_extraction
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


def test_store_extraction_persists_completed_graph():
    session = FakeSession()
    extraction_id = uuid4()
    page_id = uuid4()
    snapshot = make_snapshot()
    result = ExtractionResult(
        persons=[
            PersonResult(
                name="Jane Doe",
                countries=["Utopia"],
                positions=[PositionResult(name="Chair", organization="Board")],
            )
        ]
    )

    extraction = store_extraction(
        session,
        extraction_id=extraction_id,
        page_id=page_id,
        model="test-model",
        snapshot=snapshot,
        result=result,
    )

    assert isinstance(extraction, Extraction)
    assert extraction.id == extraction_id
    assert extraction.page_id == page_id
    assert extraction.model == "test-model"
    assert extraction.snapshot_id == snapshot.id
    assert extraction.captured_at == snapshot.captured_at
    assert extraction.extracted_at is not None
    assert session.added == [extraction]

    persons = extraction.persons
    assert [p.name for p in persons] == ["Jane Doe"]
    assert [p.extraction_id for p in persons] == [extraction_id]
    positions: list[Position] = persons[0].positions
    assert [pos.name for pos in positions] == ["Chair"]
    assert positions[0].organization == "Board"


def test_store_extraction_without_persons():
    session = FakeSession()

    extraction = store_extraction(
        session,
        extraction_id=uuid4(),
        page_id=uuid4(),
        model="test-model",
        snapshot=make_snapshot(),
        result=ExtractionResult(persons=[]),
    )

    assert extraction.persons == []
    assert all(not isinstance(obj, Person) for obj in session.added)
