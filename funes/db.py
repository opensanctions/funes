"""Durable page catalogue and inspection-ledger persistence."""

import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pravda import Snapshot
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    func,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

# The Pydantic structured-output result from funes.extract, aliased to
# avoid colliding with the ORM Inspection model below.
from funes.extract import Extraction as ExtractionResult


class Outcome(StrEnum):
    """Terminal outcome of one pipeline run over a page.

    - productive: captured + extracted, at least one person. Graph persisted.
    - empty: captured + extracted cleanly, zero persons. No graph.
    - broken: no inspectable result. ``reason`` is set; ``model`` and
      ``extracted_at`` are NULL when the model never ran (broken at
      capture), and ``model`` is set when the model itself concluded the
      page is broken. Infra crashes (exceptions) write no row at all —
      retrying them is Procrastinate's business, not a page fact.
    """

    PRODUCTIVE = "productive"
    EMPTY = "empty"
    BROKEN = "broken"


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
    inspections: Mapped[list["Inspection"]] = relationship(
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


class Inspection(Base):
    """One terminal outcome of a pipeline run over a page."""

    __tablename__ = "inspection"
    __table_args__ = (Index("ix_inspection_page_created", "page_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("page.id", ondelete="CASCADE")
    )
    snapshot_id: Mapped[uuid.UUID]
    outcome: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    page: Mapped[Page] = relationship(back_populates="inspections")
    persons: Mapped[list["Person"]] = relationship(
        back_populates="inspection", cascade="all, delete-orphan"
    )


class Person(Base):
    """One person observed in an inspection, with person-level facts."""

    __tablename__ = "person"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    inspection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inspection.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    dob: Mapped[str | None] = mapped_column(Text)
    bio: Mapped[str | None] = mapped_column(Text)
    countries: Mapped[list[str]] = mapped_column(JSON)

    inspection: Mapped[Inspection] = relationship(back_populates="persons")
    positions: Mapped[list["Position"]] = relationship(
        back_populates="person", cascade="all, delete-orphan"
    )


class Position(Base):
    """One position held by a person within an inspection."""

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


async def select_due_pages(
    session: AsyncSession, revisit_interval: timedelta
) -> list[Page]:
    """Return pages due for a new pipeline run, ordered by URL.

    A page is due iff (a) it has no inspections (never attempted), or
    (b) its latest inspection (by created_at) is productive and older than
    ``revisit_interval``. Empty and broken latest verdicts make a page not
    due.
    """
    latest_created_at = (
        select(
            Inspection.page_id.label("page_id"),
            func.max(Inspection.created_at).label("created_at"),
        )
        .group_by(Inspection.page_id)
        .subquery()
    )
    latest = (
        select(Inspection.page_id, Inspection.outcome, Inspection.created_at)
        .join(
            latest_created_at,
            (Inspection.page_id == latest_created_at.c.page_id)
            & (Inspection.created_at == latest_created_at.c.created_at),
        )
        .subquery()
    )
    cutoff = datetime.now(UTC) - revisit_interval
    stmt = (
        select(Page)
        .outerjoin(latest, latest.c.page_id == Page.id)
        .where(
            or_(
                latest.c.page_id.is_(None),
                (latest.c.outcome == Outcome.PRODUCTIVE)
                & (latest.c.created_at < cutoff),
            )
        )
        .order_by(Page.url)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


def store_inspection(
    session: AsyncSession,
    *,
    inspection_id: uuid.UUID,
    page_id: uuid.UUID,
    snapshot: Snapshot,
    outcome: Outcome,
    model: str | None = None,
    reason: str | None = None,
    result: ExtractionResult | None = None,
) -> Inspection:
    """Construct and persist one terminal pipeline outcome.

    Productive/empty outcomes require ``model`` and ``result`` (the graph is
    mapped from ``result``) and forbid ``reason``; broken outcomes require
    ``reason``, forbid ``result``, and set ``model`` iff the model ran.
    """
    if outcome in (Outcome.PRODUCTIVE, Outcome.EMPTY):
        if model is None:
            raise ValueError(f"{outcome} outcome requires a model")
        if result is None:
            raise ValueError(f"{outcome} outcome requires a result")
        if reason is not None:
            raise ValueError(f"{outcome} outcome must not set a reason")
        extracted_at: datetime | None = datetime.now(UTC)
    elif outcome is Outcome.BROKEN:
        if reason is None:
            raise ValueError("broken outcome requires a reason")
        if result is not None:
            raise ValueError("broken outcome must not set a result")
        extracted_at = None
    else:
        raise ValueError(f"unknown outcome: {outcome!r}")

    inspection = Inspection(
        id=inspection_id,
        page_id=page_id,
        snapshot_id=snapshot.id,
        outcome=outcome,
        reason=reason,
        model=model,
        captured_at=snapshot.captured_at,
        extracted_at=extracted_at,
    )
    persons: list[Person] = []
    if result is not None:
        persons = [
            Person(
                inspection_id=inspection_id,
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
    inspection.persons = persons
    session.add(inspection)
    return inspection
