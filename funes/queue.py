"""Procrastinate queue wiring: app factory, queue name, and DSN conversion."""

from procrastinate import App, PsycopgConnector
from sqlalchemy.engine import make_url

from funes.config import Config
from funes.tasks import QUEUE_PIPELINE, TASK_PROCESS_EXTRACTION, register_tasks

__all__ = ["QUEUE_PIPELINE", "TASK_PROCESS_EXTRACTION", "build_app", "to_conninfo"]


def to_conninfo(database_url: str) -> str:
    """Convert a SQLAlchemy ``postgresql+psycopg`` URL to a libpq DSN."""
    url = make_url(database_url).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


def build_app(config: Config) -> App:
    """Construct the Procrastinate app with Funes's single explicit task.

    Constructed with an already-loaded Config so no env vars are read at import
    time; calling this repeatedly yields equivalent, independent apps.
    """
    app: App = App(
        connector=PsycopgConnector(conninfo=to_conninfo(config.pravda.database_url))
    )
    register_tasks(app, config)
    return app
