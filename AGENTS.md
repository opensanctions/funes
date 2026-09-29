# Funes

Funes embeds [Pravda](https://github.com/opensanctions/pravda) (PyPI: `opensanctions-pravda`) as an in-process async library for page capture and storage, and queues candidate-inspection jobs through Procrastinate. Funes owns the infrastructure Pravda connects to: an async Postgres database and an fsspec artifact store; the browser is any Playwright Chromium WebSocket endpoint (see `.env.example`).

## Project philosophy

- Early-stage. No backward compatibility, no fallback behaviors. Fail loud: no `try/except` without a specific reason.
- Development infrastructure (Postgres, browser, artifact store) is shared. Do not create ad-hoc databases or browsers for tests.

## Commands

```bash
uv sync                  # install dependencies
docker compose up -d     # shared dev infrastructure: Postgres (the browser is external)
uv run --env-file .env alembic upgrade head             # apply the full schema
uv run --env-file .env funes seed                       # append-only bootstrap of candidates from YAML
uv run --env-file .env funes enqueue      # queue one job per due candidate
uv run --env-file .env procrastinate worker --queues inspect  # capture/extract only; discovery and repair jobs stay pending
uv run --env-file .env procrastinate worker --queues discovery  # dedicated discovery worker: discover_links runs per usable attempt
uv run --env-file .env procrastinate shell list_jobs    # inspect the queue
uv run --env-file .env pytest             # run the test suite
```

- Dependencies are added with `uv add`. Don't edit `pyproject.toml` manually.
- Env vars are read through `config.py`'s `load_config()`, never with ad-hoc `os.environ` reads.

## Conventions

- No lazy imports unless there's a real cost.
- True constants (paths, format strings) live in the module that uses them.

## Testing

Test behavior, not implementation. Prefer lean integration tests that exercise each module's public interface the way the pipeline uses it, over unit tests that pin internals. Follow pydantic-ai's testing guidance, since the extraction pipeline is a pydantic-ai agent:

- No mocks of our own code. Use fakes at the boundaries: fsspec's `memory://` filesystem for artifacts, a stub session for persistence.
- Replace the LLM with `TestModel` for schema-satisfying runs or `FunctionModel` for scripted model behavior, swapped in with `Agent.override`.
