"""candidate provenance

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-26 10:00:00.000000

Discovery-selected candidates record their provenance: the id of the
attempt whose discovery run selected them (a logical reference into the
same attempt table, deliberately without a foreign key) and the selecting
model's reason. Null provenance means the candidate came from the
catalogue bootstrap.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("candidate", sa.Column("attempt_id", sa.Uuid(), nullable=True))
    op.add_column("candidate", sa.Column("reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("candidate", "reason")
    op.drop_column("candidate", "attempt_id")
