"""Funes-owned pipeline runs, extractions, and observations."""

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

EXTRACTION_PENDING = "pending"
EXTRACTION_SUCCEEDED = "succeeded"
EXTRACTION_FAILED = "failed"

ERROR_CAPTURE = "capture"
ERROR_EXTRACT = "extract"


class Base(DeclarativeBase):
    pass


class Run(Base):
    """One invocation of the pipeline."""

    __tablename__ = "run"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    extractions: Mapped[list["Extraction"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class Extraction(Base):
    """One URL selected for capture and extraction within a run."""

    __tablename__ = "extraction"
    __table_args__ = (
        UniqueConstraint("run_id", "url"),
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed')",
            name="extraction_status",
        ),
        CheckConstraint(
            "(status = 'pending' AND snapshot_id IS NULL AND error_stage IS NULL "
            "AND error IS NULL AND captured_at IS NULL AND extracted_at IS NULL) OR "
            "(status = 'succeeded' AND snapshot_id IS NOT NULL "
            "AND error_stage IS NULL AND error IS NULL AND captured_at IS NOT NULL "
            "AND extracted_at IS NOT NULL) OR "
            "(status = 'failed' AND error_stage IS NOT NULL AND error IS NOT NULL "
            "AND extracted_at IS NULL AND "
            "((snapshot_id IS NULL AND captured_at IS NULL) OR "
            "(snapshot_id IS NOT NULL AND captured_at IS NOT NULL)))",
            name="extraction_outcome",
        ),
        CheckConstraint(
            "error_stage IS NULL OR error_stage IN ('capture', 'extract')",
            name="extraction_error_stage",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default=EXTRACTION_PENDING)
    snapshot_id: Mapped[uuid.UUID | None]
    error_stage: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[Run] = relationship(back_populates="extractions")
    pages: Mapped[list["Page"]] = relationship(
        back_populates="extraction", cascade="all, delete-orphan"
    )
    holders: Mapped[list["Holder"]] = relationship(
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


class Holder(Base):
    """One extracted person-position observation."""

    __tablename__ = "holder"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extraction.id", ondelete="CASCADE")
    )
    person_name: Mapped[str] = mapped_column(Text)
    position_name: Mapped[str] = mapped_column(Text)
    person_dob: Mapped[str | None] = mapped_column(Text)
    person_bio: Mapped[str | None] = mapped_column(Text)
    person_countries: Mapped[list[str]] = mapped_column(JSON)
    position_organization: Mapped[str | None] = mapped_column(Text)
    position_description: Mapped[str | None] = mapped_column(Text)
    position_jurisdiction: Mapped[str | None] = mapped_column(Text)
    position_start_date: Mapped[str | None] = mapped_column(Text)
    position_end_date: Mapped[str | None] = mapped_column(Text)

    extraction: Mapped[Extraction] = relationship(back_populates="holders")


async def register_extractions(
    session: AsyncSession,
    run: Run,
    associations: list[tuple[str, str, str]],
    model: str,
) -> dict[str, Extraction]:
    """Add one extraction per URL and attach all selected input associations."""
    await session.flush()
    extractions = {
        url: Extraction(run_id=run.id, url=url, model=model)
        for _, url, _ in associations
    }
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
    holders: list[dict],
) -> None:
    """Store a successful extraction and all of its holder observations."""
    _require_pending(extraction)
    extraction.status = EXTRACTION_SUCCEEDED
    extraction.snapshot_id = snapshot.id
    extraction.captured_at = snapshot.captured_at
    extraction.extracted_at = datetime.now(UTC)
    session.add_all(Holder(extraction_id=extraction.id, **holder) for holder in holders)


def extraction_failed(
    extraction: Extraction,
    stage: str,
    error: str,
    snapshot: Snapshot | None = None,
) -> None:
    """Record a capture or extraction failure."""
    _require_pending(extraction)
    if stage not in {ERROR_CAPTURE, ERROR_EXTRACT}:
        raise ValueError(f"unknown extraction error stage: {stage}")
    extraction.status = EXTRACTION_FAILED
    extraction.error_stage = stage
    extraction.error = error
    if snapshot is not None:
        extraction.snapshot_id = snapshot.id
        extraction.captured_at = snapshot.captured_at


def _require_pending(extraction: Extraction) -> None:
    if extraction.status != EXTRACTION_PENDING:
        raise ValueError(f"extraction {extraction.id} is already {extraction.status}")
