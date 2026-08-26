"""Durable page catalogue and completed-extraction persistence."""

import uuid
from datetime import UTC, datetime

from pravda import Snapshot
from sqlalchemy import JSON, DateTime, ForeignKey, Text, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

# The Pydantic structured-output result from funes.extract, aliased to
# avoid colliding with the ORM Extraction model below.
from funes.extract import Extraction as ExtractionResult


class Base(DeclarativeBase):
    pass


class Page(Base):
    """One durable web page, identified by URL, with its organizations."""

    __tablename__ = "page"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    url: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    organizations: Mapped[list["PageOrganization"]] = relationship(
        back_populates="page", cascade="all, delete-orphan"
    )
    extractions: Mapped[list["Extraction"]] = relationship(
        back_populates="page", cascade="all, delete-orphan"
    )


class PageOrganization(Base):
    """One organization associated with a page."""

    __tablename__ = "page_organization"
    __table_args__ = (UniqueConstraint("page_id", "organization"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("page.id", ondelete="CASCADE")
    )
    organization: Mapped[str] = mapped_column(Text)

    page: Mapped[Page] = relationship(back_populates="organizations")


class Extraction(Base):
    """One completed extraction of a page, with its result graph."""

    __tablename__ = "extraction"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("page.id", ondelete="CASCADE")
    )
    model: Mapped[str] = mapped_column(Text)
    snapshot_id: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    page: Mapped[Page] = relationship(back_populates="extractions")
    persons: Mapped[list["Person"]] = relationship(
        back_populates="extraction", cascade="all, delete-orphan"
    )


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


async def import_pages(session: AsyncSession, rows: list[tuple[str, str]]) -> None:
    """Append-only import of (url, organization) rows into the page catalogue.

    Creates missing pages and page-organization associations; existing rows
    are left untouched (insert-on-conflict does nothing). No updates or
    deletes are ever performed.
    """
    urls = list(dict.fromkeys(url for url, _ in rows))
    if not urls:
        return
    await session.execute(
        insert(Page)
        .values([{Page.url: url} for url in urls])
        .on_conflict_do_nothing(index_elements=[Page.url])
    )
    # Map url -> id for association inserts.
    id_by_url = dict(
        (
            await session.execute(select(Page.url, Page.id).where(Page.url.in_(urls)))
        ).all()
    )
    associations = [
        {PageOrganization.page_id: id_by_url[url], PageOrganization.organization: org}
        for url, org in rows
        if org
    ]
    if associations:
        await session.execute(
            insert(PageOrganization)
            .values(associations)
            .on_conflict_do_nothing(
                index_elements=[PageOrganization.page_id, PageOrganization.organization]
            )
        )


async def select_pages(session: AsyncSession) -> list[Page]:
    """Return all catalogue pages."""
    result = await session.execute(select(Page).order_by(Page.url))
    return list(result.scalars().all())


def store_extraction(
    session: AsyncSession,
    *,
    extraction_id: uuid.UUID,
    page_id: uuid.UUID,
    model: str,
    snapshot: Snapshot,
    result: ExtractionResult,
) -> Extraction:
    """Construct and persist one successful extraction and its nested graph."""
    extraction = Extraction(
        id=extraction_id,
        page_id=page_id,
        model=model,
        snapshot_id=snapshot.id,
        captured_at=snapshot.captured_at,
        extracted_at=datetime.now(UTC),
    )
    extraction.persons = [
        Person(
            extraction_id=extraction_id,
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
    ]
    session.add(extraction)
    return extraction
