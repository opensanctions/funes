"""extraction outcome state

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-25 18:00:00.000000

Tracks whether each extraction was successfully extracted or recorded as
broken. Renames extracted_at to processed_at (it now marks processing of
either outcome), adds the outcome and broken_reason columns, backfills
outcome for already-processed rows, and replaces the timestamp check
constraint with the outcome-state constraint.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OUTCOME_CONSTRAINT = (
    "(snapshot_id IS NULL AND captured_at IS NULL AND outcome IS NULL "
    "AND processed_at IS NULL AND broken_reason IS NULL) "
    "OR (snapshot_id IS NOT NULL AND captured_at IS NOT NULL "
    "AND processed_at IS NOT NULL "
    "AND outcome IN ('extracted', 'broken') "
    "AND (outcome <> 'extracted' OR broken_reason IS NULL) "
    "AND (outcome <> 'broken' OR broken_reason IS NOT NULL))"
)


def upgrade() -> None:
    op.alter_column("extraction", "extracted_at", new_column_name="processed_at")
    op.add_column("extraction", sa.Column("outcome", sa.Text(), nullable=True))
    op.add_column("extraction", sa.Column("broken_reason", sa.Text(), nullable=True))
    # Rows processed under the old schema were by definition successful.
    op.execute(
        "UPDATE extraction SET outcome = 'extracted' WHERE processed_at IS NOT NULL"
    )
    op.drop_constraint("extraction_timestamps", "extraction")
    op.create_check_constraint("extraction_outcome", "extraction", OUTCOME_CONSTRAINT)


def downgrade() -> None:
    op.drop_constraint("extraction_outcome", "extraction")
    op.drop_column("extraction", "broken_reason")
    op.drop_column("extraction", "outcome")
    op.alter_column("extraction", "processed_at", new_column_name="extracted_at")
    op.create_check_constraint(
        "extraction_timestamps",
        "extraction",
        "(snapshot_id IS NULL AND captured_at IS NULL AND extracted_at IS NULL) "
        "OR (snapshot_id IS NOT NULL AND captured_at IS NOT NULL "
        "AND extracted_at IS NOT NULL)",
    )
