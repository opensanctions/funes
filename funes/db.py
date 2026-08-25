"""Funes-owned extractions and their nested person/position graph."""

import uuid
from datetime import UTC, datetime

from pravda import Snapshot
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# The Pydantic structured-output result from funes.extract, aliased to
# avoid colliding with the ORM Extraction model below.
from funes.extract import Extraction as ExtractionResult


class Base(DeclarativeBase):
    pass


class Extraction(Base):
    """One URL selected for capture and extraction, with its result graph."""

    __tablename__ = "extraction"
    __table_args__ = (
        CheckConstraint(
            "(snapshot_id IS NULL AND captured_at IS NULL AND extracted_at IS NULL) OR "
            "(snapshot_id IS NOT NULL AND captured_at IS NOT NULL "
            "AND extracted_at IS NOT NULL)",
            name="extraction_timestamps",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    url: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    snapshot_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    pages: Mapped[list["Page"]] = relationship(
        back_populates="extraction", cascade="all, delete-orphan"
    )
    persons: Mapped[list["Person"]] = relationship(
        back_populates="extraction", cascade="all, delete-orphan"
    )


class Page(Base):
    """One input dataset and organization associated with an extraction."""

    __tablename__ = "page"
    __table_args__ = (UniqueConstraint("extraction_id", "dataset"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extraction.id", ondelete="CASCADE")
    )
    dataset: Mapped[str] = mapped_column(Text)
    organization: Mapped[str] = mapped_column(Text)

    extraction: Mapped[Extraction] = relationship(back_populates="pages")


class Person(Base):
    """One person observed in an extraction, with person-level facts."""

    __tablename__ = "person"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extraction.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    dob: Mapped[str | None] = mapped_column(Text)
    bio: Mapped[str | None] = mapped_column(Text)
    countries: Mapped[list[str]] = mapped_column(JSON)

    extraction: Mapped[Extraction] = relationship(back_populates="persons")
    positions: Mapped[list["Position"]] = relationship(
        back_populates="person", cascade="all, delete-orphan"
    )


class Position(Base):
    """One position held by a person within an extraction."""

    __tablename__ = "position"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    organization: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    jurisdiction: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[str | None] = mapped_column(Text)
    end_date: Mapped[str | None] = mapped_column(Text)

    person: Mapped[Person] = relationship(back_populates="positions")


async def register_extractions(
    session: AsyncSession,
    associations: list[tuple[str, str, str]],
    model: str,
) -> dict[str, Extraction]:
    """Add one extraction per URL and attach all selected input associations."""
    extractions = {url: Extraction(url=url, model=model) for _, url, _ in associations}
    session.add_all(extractions.values())
    await session.flush()
    session.add_all(
        Page(
            extraction_id=extractions[url].id,
            dataset=dataset,
            organization=organization,
        )
        for dataset, url, organization in associations
    )
    return extractions


def extraction_succeeded(
    session: AsyncSession,
    extraction: Extraction,
    snapshot: Snapshot,
    result: ExtractionResult,
) -> None:
    """Store a successful extraction and its nested person/position graph."""
    extraction.snapshot_id = snapshot.id
    extraction.captured_at = snapshot.captured_at
    extraction.extracted_at = datetime.now(UTC)
    session.add_all(
        Person(
            extraction_id=extraction.id,
            name=person.name,
            dob=person.dob,
            bio=person.bio,
            countries=person.countries,
            positions=[
                Position(
                    name=position.name,
                    organization=position.organization,
                    description=position.description,
                    jurisdiction=position.jurisdiction,
                    start_date=position.start_date,
                    end_date=position.end_date,
                )
                for position in person.positions
            ],
        )
        for person in result.persons
    )
