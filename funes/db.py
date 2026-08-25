"""Funes-owned durable run state in PostgreSQL."""

import uuid
from datetime import UTC, datetime

from pravda import Snapshot
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

CAPTURE_PENDING = "pending"
CAPTURE_SUCCEEDED = "succeeded"
CAPTURE_FAILED = "failed"


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


class Capture(Base):
    """The capture state of one exact URL within a run."""

    __tablename__ = "capture"
    __table_args__ = (
        UniqueConstraint("run_id", "url"),
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed')",
            name="capture_status",
        ),
        CheckConstraint(
            "(status = 'pending' AND snapshot_id IS NULL AND error IS NULL "
            "AND captured_at IS NULL) OR "
            "(status = 'succeeded' AND snapshot_id IS NOT NULL AND error IS NULL "
            "AND captured_at IS NOT NULL) OR "
            "(status = 'failed' AND error IS NOT NULL "
            "AND ((snapshot_id IS NULL AND captured_at IS NULL) OR "
            "(snapshot_id IS NOT NULL AND captured_at IS NOT NULL)))",
            name="capture_outcome",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default=CAPTURE_PENDING)
    snapshot_id: Mapped[uuid.UUID | None]
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Page(Base):
    """One dataset and organization association with a captured URL."""

    __tablename__ = "page"
    __table_args__ = (UniqueConstraint("capture_id", "dataset"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    capture_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("capture.id", ondelete="CASCADE")
    )
    dataset: Mapped[str] = mapped_column(Text)
    organization: Mapped[str] = mapped_column(Text)


async def register_pages(
    session: AsyncSession,
    run: Run,
    associations: list[tuple[str, str, str]],
) -> dict[str, Capture]:
    """Add one capture per URL and one page per selected association."""
    await session.flush()
    captures = {
        url: Capture(id=uuid.uuid4(), run_id=run.id, url=url)
        for _, url, _ in associations
    }
    session.add_all(captures.values())
    await session.flush()
    session.add_all(
        Page(
            id=uuid.uuid4(),
            capture_id=captures[url].id,
            dataset=dataset,
            organization=organization,
        )
        for dataset, url, organization in associations
    )
    return captures


def capture_succeeded(capture: Capture, snapshot: Snapshot) -> None:
    """Record a successful Pravda snapshot on a pending capture."""
    _require_pending(capture)
    capture.status = CAPTURE_SUCCEEDED
    capture.snapshot_id = snapshot.id
    capture.captured_at = snapshot.captured_at


def capture_failed(
    capture: Capture, error: str, snapshot: Snapshot | None = None
) -> None:
    """Record an exact snapshot or operational error on a pending capture."""
    _require_pending(capture)
    capture.status = CAPTURE_FAILED
    capture.error = error
    if snapshot is not None:
        capture.snapshot_id = snapshot.id
        capture.captured_at = snapshot.captured_at


def _require_pending(capture: Capture) -> None:
    if capture.status != CAPTURE_PENDING:
        raise ValueError(f"capture {capture.id} is already {capture.status}")
