"""procrastinate schema

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-25 17:00:00.000000

Installs the complete Procrastinate job-queue schema for the pinned
Procrastinate version (3.9.0). Procrastinate ships no migration ledger of
its own, so Funes's Alembic ledger tracks it on Procrastinate's behalf:
this revision executes the immutable vendored SQL asset verbatim (copied
from procrastinate/sql/schema.sql at pin time) rather than reading the
installed package, so the migration stays reproducible across Procrastinate
upgrades. Future Procrastinate bumps get a new vendored asset and a new
Funes revision.
"""

from collections.abc import Sequence
from importlib.resources import files

import sqlalchemy as sa
import sqlalchemy.util
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_ASSET = "procrastinate_schema_3.9.0.sql"

PROCRASTINATE_FUNCTIONS = (
    "procrastinate_defer_jobs_v1",
    "procrastinate_defer_periodic_job_v2",
    "procrastinate_fetch_job_v2",
    "procrastinate_finish_job_v1",
    "procrastinate_cancel_job_v1",
    "procrastinate_retry_job_v1",
    "procrastinate_retry_job_v2",
    "procrastinate_notify_queue_job_inserted_v1",
    "procrastinate_notify_queue_abort_job_v1",
    "procrastinate_trigger_function_status_events_insert_v1",
    "procrastinate_trigger_function_status_events_update_v1",
    "procrastinate_trigger_function_scheduled_events_v1",
    "procrastinate_trigger_abort_requested_events_procedure_v1",
    "procrastinate_unlink_periodic_defers_v1",
    "procrastinate_register_worker_v1",
    "procrastinate_unregister_worker_v1",
    "procrastinate_update_heartbeat_v1",
    "procrastinate_prune_stalled_workers_v1",
)
PROCRASTINATE_TABLES = (
    "procrastinate_jobs",
    "procrastinate_events",
    "procrastinate_periodic_defers",
    "procrastinate_workers",
)
PROCRASTINATE_TYPES = (
    "procrastinate_job_status",
    "procrastinate_job_event_type",
    "procrastinate_job_to_defer_v1",
)


def upgrade() -> None:
    script = (
        files("funes.migrations").joinpath("sql").joinpath(SCHEMA_ASSET).read_text()
    )
    # Run the multi-statement script through the raw psycopg cursor with no
    # parameters: SQLAlchemy's exec_driver_sql would pass an empty parameter
    # set, making psycopg treat the script's `%` characters (plpgsql RAISE
    # format strings) as bad placeholders. psycopg executes a parameterless
    # multi-statement script in one round trip, DO $$ blocks included.
    # The driver connection is psycopg's AsyncCursor behind the sync facade
    # Alembic sees, so each call is bridged with await_only.
    cursor = op.get_bind().connection.driver_connection.cursor()
    try:
        sqlalchemy.util.await_only(cursor.execute(script))
    finally:
        sqlalchemy.util.await_only(cursor.close())


def downgrade() -> None:
    # Destructive by design (development data is disposable). Functions are
    # dropped explicitly first: plpgsql bodies referencing tables are not
    # tracked dependencies, so dropping the tables with CASCADE removes
    # indexes, triggers, and constraints but silently leaves the functions.
    bind = op.get_bind()
    for function in PROCRASTINATE_FUNCTIONS:
        bind.execute(sa.text(f'DROP FUNCTION IF EXISTS "{function}" CASCADE'))
    for table in PROCRASTINATE_TABLES:
        bind.execute(sa.text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
    for type_ in PROCRASTINATE_TYPES:
        bind.execute(sa.text(f'DROP TYPE IF EXISTS "{type_}" CASCADE'))
