# Funes

Funes turns raw web pages into structured data about political position holders. It embeds [Pravda](https://github.com/opensanctions/pravda) (PyPI: `opensanctions-pravda`) as an in-process async library for page capture and storage, and queues page-processing jobs through Procrastinate. Funes owns the infrastructure Pravda connects to: a headed Chrome browser, an async Postgres database, and an fsspec artifact store (see `.env.example`).

## Project philosophy

- Early-stage. No backward compatibility, no fallback behaviors. Fail loud: no `try/except` without a specific reason.
- Development data is disposable. Prefer clean, destructive schema changes over compatibility migrations.
- Development infrastructure is shared. Do not create ad-hoc databases or browsers for tests.

## Commands

```bash
uv sync                  # install dependencies
docker compose up -d     # shared dev infrastructure: Postgres + headed Chrome
uv run --env-file .env funes migrate      # apply Pravda and Funes schemas, bootstrap pages from CSVs
uv run --env-file .env funes enqueue      # queue one job per persisted page
uv run --env-file .env procrastinate worker process     # consume the process queue
uv run --env-file .env procrastinate shell list_jobs    # inspect the queue
uv run --env-file .env pytest             # run the test suite
```

- Dependencies are added with `uv add`. Don't edit `pyproject.toml` manually.
- Pre-commit hooks run `ruff check --fix` and `ruff format` on every commit.

## Project structure

```
funes/
  procrastinate.py  # module-level Procrastinate app (the PROCRASTINATE_APP target); worker config
  cli.py        # migrate, enqueue commands
  tasks.py      # Procrastinate tasks: process_page pipeline; dormant review queue
  capture.py    # Pravda client and fsspec artifact helpers
  extract.py    # pydantic-ai extraction agent, output schema, prompt
  outline.py    # compact model-facing outline from rendered HTML + HAR
  db.py         # SQLAlchemy models and persistence
  migrate.py    # Alembic runner; migrations/ holds Funes's ledger
  sources.py    # bootstrap CSV loading
  config.py     # typed configuration
  sessions.py   # agent session transcripts
tests/          # pytest suite
```

Every command runs as `uv run --env-file .env …`, so the environment comes from `.env` via uv; there is no dotenv dependency. Env vars are read through `config.py`'s `load_config()`, never with ad-hoc `os.environ` reads. One-shot commands call it per process; the long-lived worker shares the `config` loaded once at import in `funes/procrastinate.py`.

## Conventions

- Keep imports at the top of each file. No lazy imports unless there's a real cost.
- True constants (paths, format strings) live in the module that uses them.

## Testing

Test behavior, not implementation. Prefer lean integration tests that exercise each module's public interface the way the pipeline uses it, over unit tests that pin internals. Follow pydantic-ai's testing guidance, since the extraction pipeline is a pydantic-ai agent:

- pytest is the harness: `uv run pytest`.
- No mocks of our own code. Use fakes at the boundaries: fsspec's `memory://` filesystem for artifacts, a stub session for persistence (see `tests/test_db.py`).
- Replace the LLM with `TestModel` for schema-satisfying runs or `FunctionModel` for scripted model behavior, swapped in with `Agent.override` (see `tests/test_extract.py`).
- `tests/conftest.py` sets `ALLOW_MODEL_REQUESTS = False`; a test that hits a real model API fails.
- Tests never touch the shared development infrastructure (Postgres, browser, artifact store).
