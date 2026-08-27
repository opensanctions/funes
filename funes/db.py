"""Normalized objective/url candidate and attempt persistence."""

import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, ClassVar

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
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    selectinload,
)

from funes.extract import Hit, Miss


class SnapshotStatus(StrEnum):
    """Terminal assessment of a captured Pravda snapshot.

    - usable: the snapshot is a usable source; an Inspection follows.
    - broken: the snapshot is unusable. ``reason`` is required; ``model``
      is null only when deterministic checks rejected the snapshot before
      the model ran.
    """

    USABLE = "usable"
    BROKEN = "broken"


class InspectionOutcome(StrEnum):
    """Terminal judgement of a usable snapshot against an objective.

    - hit: the objective is satisfied; extracted people are attached.
    - miss: nothing satisfies the objective; ``reason`` explains why.
    """

    HIT = "hit"
    MISS = "miss"


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[type[Any], Any]] = {str: Text}


class Dataset(Base):
    """One input dataset, named after its source CSV filename stem."""

    __tablename__ = "dataset"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    objectives: Mapped[list["Objective"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan"
    )


class Objective(Base):
    """What a dataset wants to learn from pages, owned by one dataset."""

    __tablename__ = "objective"
    __table_args__ = (UniqueConstraint("dataset_id", "description"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dataset.id", ondelete="CASCADE")
    )
    description: Mapped[str] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    dataset: Mapped[Dataset] = relationship(back_populates="objectives")
    candidates: Mapped[list["Candidate"]] = relationship(
        back_populates="objective", cascade="all, delete-orphan"
    )


class Url(Base):
    """Objective-independent URL identity for snapshot capture."""

    __tablename__ = "url"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    url: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    candidates: Mapped[list["Candidate"]] = relationship(
        back_populates="url", cascade="all, delete-orphan"
    )


class Candidate(Base):
    """The objective ↔ url relation: one unit of work for the pipeline."""

    __tablename__ = "candidate"
    __table_args__ = (UniqueConstraint("objective_id", "url_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    objective_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("objective.id", ondelete="CASCADE")
    )
    url_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("url.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    objective: Mapped[Objective] = relationship(back_populates="candidates")
    url: Mapped[Url] = relationship(back_populates="candidates")
    attempts: Mapped[list["Attempt"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )


class Attempt(Base):
    """One completed domain run connecting a candidate to a Pravda snapshot.

    The snapshot stays a logical UUID reference into Pravda's own storage;
    infra failures (exceptions) create no row.
    """

    __tablename__ = "attempt"
    __table_args__ = (
        Index("ix_attempt_candidate_created", "candidate_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate.id", ondelete="CASCADE")
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    candidate: Mapped[Candidate] = relationship(back_populates="attempts")
    assessment: Mapped["SnapshotAssessment | None"] = relationship(
        back_populates="attempt", uselist=False, cascade="all, delete-orphan"
    )
    inspection: Mapped["Inspection | None"] = relationship(
        back_populates="attempt", uselist=False, cascade="all, delete-orphan"
    )


class SnapshotAssessment(Base):
    """Terminal assessment of one snapshot, keyed by its snapshot id."""

    __tablename__ = "snapshot_assessment"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("attempt.snapshot_id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[SnapshotStatus]
    reason: Mapped[str | None] = mapped_column()
    model: Mapped[str | None] = mapped_column()
    assessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    attempt: Mapped[Attempt] = relationship(back_populates="assessment", uselist=False)


class Inspection(Base):
    """The judgement of a usable snapshot against one objective."""

    __tablename__ = "inspection"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("attempt.id", ondelete="CASCADE"), unique=True
    )
    outcome: Mapped[InspectionOutcome]
    reason: Mapped[str | None] = mapped_column()
    model: Mapped[str] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    attempt: Mapped[Attempt] = relationship(back_populates="inspection")
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
    name: Mapped[str] = mapped_column()
    dob: Mapped[str | None] = mapped_column()
    bio: Mapped[str | None] = mapped_column()
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
    name: Mapped[str] = mapped_column()
    organization: Mapped[str | None] = mapped_column()
    description: Mapped[str | None] = mapped_column()
    jurisdiction: Mapped[str | None] = mapped_column()
    start_date: Mapped[str | None] = mapped_column()
    end_date: Mapped[str | None] = mapped_column()

    person: Mapped[Person] = relationship(back_populates="positions")


def _upsert_insert(session: AsyncSession):
    """Return the session dialect's INSERT constructor with ON CONFLICT support.

    Production runs on PostgreSQL; the test suite exercises the same
    statements against in-memory SQLite. Both dialects offer
    ``on_conflict_do_nothing`` with identical semantics.
    """
    match session.bind.dialect.name:
        case "postgresql":
            return postgres_insert
        case "sqlite":
            return sqlite_insert
        case dialect:
            raise ValueError(f"unsupported dialect: {dialect!r}")


async def import_candidates(
    session: AsyncSession, rows: list[tuple[str, str, str]]
) -> None:
    """Append-only import of ``(dataset, objective, url)`` rows.

    Creates missing datasets, objectives, urls, and candidates; existing
    rows are left untouched (insert-on-conflict does nothing). No updates
    or deletes are ever performed.
    """
    if not rows:
        return
    insert = _upsert_insert(session)
    names = list(dict.fromkeys(dataset for dataset, _, _ in rows))
    descriptions = list(dict.fromkeys(objective for _, objective, _ in rows))
    urls = list(dict.fromkeys(url for _, _, url in rows))
    await session.execute(
        insert(Dataset)
        .values([{Dataset.name: name} for name in names])
        .on_conflict_do_nothing(index_elements=[Dataset.name])
    )
    await session.execute(
        insert(Url)
        .values([{Url.url: url} for url in urls])
        .on_conflict_do_nothing(index_elements=[Url.url])
    )
    dataset_id_by_name = dict(
        (
            await session.execute(
                select(Dataset.name, Dataset.id).where(Dataset.name.in_(names))
            )
        ).all()
    )
    url_id_by_url = dict(
        (await session.execute(select(Url.url, Url.id).where(Url.url.in_(urls)))).all()
    )
    objective_keys = sorted(
        {(dataset_id_by_name[dataset], description) for dataset, description, _ in rows}
    )
    await session.execute(
        insert(Objective)
        .values(
            [
                {
                    Objective.dataset_id: dataset_id,
                    Objective.description: description,
                }
                for dataset_id, description in objective_keys
            ]
        )
        .on_conflict_do_nothing(
            index_elements=[Objective.dataset_id, Objective.description]
        )
    )
    objective_id_by_key = {
        (dataset_id, description): objective_id
        for dataset_id, description, objective_id in (
            await session.execute(
                select(Objective.dataset_id, Objective.description, Objective.id).where(
                    Objective.dataset_id.in_(dataset_id_by_name.values()),
                    Objective.description.in_(descriptions),
                )
            )
        ).all()
    }
    await session.execute(
        insert(Candidate)
        .values(
            [
                {
                    Candidate.objective_id: objective_id_by_key[
                        (dataset_id_by_name[dataset], description)
                    ],
                    Candidate.url_id: url_id_by_url[url],
                }
                for dataset, description, url in rows
            ]
        )
        .on_conflict_do_nothing(
            index_elements=[Candidate.objective_id, Candidate.url_id]
        )
    )


async def select_due_candidates(
    session: AsyncSession, revisit_interval: timedelta
) -> list[Candidate]:
    """Return candidates due for a new run, in deterministic order.

    A candidate is due iff it has no attempt, or its latest attempt (by
    created_at, ties broken by id) carries a HIT inspection and is older
    than ``revisit_interval``. A latest MISS inspection or BROKEN
    assessment blocks the normal queue; broken snapshots are the repair
    path's business, not this one.
    """
    # Deterministic latest attempt per candidate: newest created_at wins,
    # ties broken by the greater id.
    ranked = select(
        Attempt.candidate_id.label("candidate_id"),
        Attempt.id.label("attempt_id"),
        Attempt.created_at.label("created_at"),
        func.row_number()
        .over(
            partition_by=Attempt.candidate_id,
            order_by=(Attempt.created_at.desc(), Attempt.id.desc()),
        )
        .label("rank"),
    ).subquery()
    latest = (
        select(ranked.c.candidate_id, ranked.c.attempt_id, ranked.c.created_at)
        .where(ranked.c.rank == 1)
        .subquery()
    )
    cutoff = datetime.now(UTC) - revisit_interval
    stmt = (
        select(Candidate)
        .options(
            selectinload(Candidate.objective).selectinload(Objective.dataset),
            selectinload(Candidate.url),
        )
        .join(Objective, Objective.id == Candidate.objective_id)
        .join(Dataset, Dataset.id == Objective.dataset_id)
        .join(Url, Url.id == Candidate.url_id)
        .outerjoin(latest, latest.c.candidate_id == Candidate.id)
        .where(
            or_(
                latest.c.candidate_id.is_(None),
                (latest.c.created_at < cutoff)
                & latest.c.attempt_id.in_(
                    select(Inspection.attempt_id).where(
                        Inspection.outcome == InspectionOutcome.HIT
                    )
                ),
            )
        )
        .order_by(Dataset.name, Objective.description, Url.url)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


def store_broken_attempt(
    session: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    candidate_id: uuid.UUID,
    snapshot: Snapshot,
    reason: str,
    model: str | None = None,
) -> Attempt:
    """Construct one terminal broken-snapshot aggregate and add it.

    Creates the Attempt (with the caller-allocated ``attempt_id`` — the
    Pydantic run/session id and repair routing id) and its BROKEN
    SnapshotAssessment; no Inspection or person graph. ``reason`` comes
    from inspectability checks or a validated BrokenSnapshot; ``model``
    is set only when the model itself concluded the snapshot is broken.
    """
    attempt = Attempt(
        id=attempt_id,
        candidate_id=candidate_id,
        snapshot_id=snapshot.id,
        captured_at=snapshot.captured_at,
    )
    attempt.assessment = SnapshotAssessment(
        snapshot_id=snapshot.id,
        status=SnapshotStatus.BROKEN,
        reason=reason,
        model=model,
    )
    session.add(attempt)
    return attempt


def store_inspection(
    session: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    candidate_id: uuid.UUID,
    snapshot: Snapshot,
    result: Hit | Miss,
    model: str,
) -> Inspection:
    """Construct one terminal Hit/Miss model-result aggregate and add it.

    Creates the Attempt (with the caller-allocated ``attempt_id``), its
    USABLE SnapshotAssessment, and the Inspection. A Hit maps the
    person/position graph and must not carry a reason; a Miss carries its
    reason and no people. ``model`` is required.
    """
    if isinstance(result, Hit):
        outcome = InspectionOutcome.HIT
        reason = None
        persons = [
            Person(
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
    else:
        outcome = InspectionOutcome.MISS
        reason = result.reason
        persons = []
    attempt = Attempt(
        id=attempt_id,
        candidate_id=candidate_id,
        snapshot_id=snapshot.id,
        captured_at=snapshot.captured_at,
    )
    attempt.assessment = SnapshotAssessment(
        snapshot_id=snapshot.id,
        status=SnapshotStatus.USABLE,
        reason=None,
        model=model,
    )
    inspection = Inspection(
        attempt=attempt,
        outcome=outcome,
        reason=reason,
        model=model,
        persons=persons,
    )
    session.add(attempt)
    return inspection
