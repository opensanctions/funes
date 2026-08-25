"""initial

Revision ID: 0001
Revises:
Create Date: 2026-08-25 16:40:34.890113
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "extraction",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(snapshot_id IS NULL AND captured_at IS NULL AND extracted_at IS NULL) "
            "OR (snapshot_id IS NOT NULL AND captured_at IS NOT NULL "
            "AND extracted_at IS NOT NULL)",
            name="extraction_timestamps",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    op.create_table(
        "page",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("extraction_id", sa.Uuid(), nullable=False),
        sa.Column("dataset", sa.Text(), nullable=False),
        sa.Column("organization", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["extraction_id"], ["extraction.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("extraction_id", "dataset"),
    )
    op.create_table(
        "person",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("extraction_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("dob", sa.Text(), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("countries", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["extraction_id"], ["extraction.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "position",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("organization", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("jurisdiction", sa.Text(), nullable=True),
        sa.Column("start_date", sa.Text(), nullable=True),
        sa.Column("end_date", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["person_id"], ["person.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("position")
    op.drop_table("person")
    op.drop_table("page")
    op.drop_table("extraction")
