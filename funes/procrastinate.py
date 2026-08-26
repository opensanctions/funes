"""The module-level Procrastinate app, importable at a stable dotted path.

Procrastinate's own CLI (worker, defer, shell, healthchecks) addresses the
app through this module — via ``PROCRASTINATE_APP=funes.procrastinate.app``
in ``.env``, injected by ``uv run --env-file .env`` — so the app and the
worker's configuration are loaded once at import time
rather than built by a factory. Tasks register themselves in
:mod:`funes.tasks`, which this app imports through ``import_paths``.
"""

from procrastinate import App, PsycopgConnector
from sqlalchemy.engine import make_url

from funes.config import load_config

config = load_config()
dsn = (
    make_url(config.pravda.database_url)
    .set(drivername="postgresql")
    .render_as_string(hide_password=False)
)

app = App(
    connector=PsycopgConnector(conninfo=dsn),
    import_paths=["funes.tasks"],
)
