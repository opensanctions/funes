# Funes

Funes is an orchestrator that turns raw web pages into structured data about political position holders. It is built on top of Pravda, the evidence layer that captures and stores durable snapshots of web pages.

## Project philosophy

- Early-stage. No backward compatibility. No fallback behaviors. Fail loud: no `try/except` unless there's a specific reason. We want errors to surface immediately.
- Development data is disposable. Prefer clean, destructive schema changes over compatibility migrations; databases and artifacts can be rebuilt when needed.

## Stack

- **Python** 3.13+ managed by **uv**.
- **Pravda** ([github.com/opensanctions/pravda](https://github.com/opensanctions/pravda)), published on PyPI as `opensanctions-pravda` (imported as `pravda`), for web page capture and storage, embedded as an in-process async library. Funes owns the infrastructure Pravda connects to — a headed Chrome browser (remote Playwright server), an async Postgres database, and an fsspec artifact store. Connection settings are `PRAVDA_DATABASE_URL`, `PRAVDA_BROWSER_WS_URL`, and `PRAVDA_STORAGE_BASE_PATH` (see `.env`). Funes constructs Pravda's `PravdaConfig` from worker configuration, reads artifacts from the shared storage backend over fsspec, and applies Pravda's packaged migrations (`pravda.migrate`) through the explicit `funes migrate` release command.
- Development infrastructure is shared. Do not create ad-hoc databases or browsers for tests.
- Funes queues extraction jobs through Procrastinate and persists each successful extraction with nested extraction-scoped persons and their positions in the PostgreSQL database shared with Pravda.

## Project structure

```
funes/           # the package: cli.py (migrate, enqueue, worker), tasks.py
                   # (Procrastinate app + pipeline task), capture.py (Pravda
                   # client + artifact helpers), extract.py, db.py, sources.py,
                   # config.py
evaluate.py        # score the extraction pipeline against synthetic fixtures
fixtures/          # one directory per fixture: page.html, expected.json, optional screenshot.png
```

`input/` (gitignored) holds the input CSVs (one dataset per file), an fsspec
path set via `INPUT_BASE_PATH` in `.env`.

## Conventions

- Dependencies are added with `uv add`. Don't edit `pyproject.toml` manually.
- Keep imports at the top of each file. No lazy imports unless there's a real cost.
- Environment-specific config goes in `.env`, loaded by `python-dotenv`.
- Read env vars with `os.environ` in the module that needs them.
- True constants (paths, format strings, etc.) live in the module that uses them.

## Running

```bash
# Install dependencies
uv sync

# Run a script
uv run python some_script.py
```

## Evaluation

Score the extraction pipeline against hand-authored fixture pages. Each
fixture is a directory under `fixtures/` holding `page.html`, an
`expected.json` answer key, and an optional `screenshot.png` that drives the
image path. The harness derives the plaintext the model reads, runs the real
`extract()`, and scores the returned (human, position) pairs by exact string
equality.

```bash
uv run python evaluate.py   # run all fixtures
uv run python evaluate.py -v # show expected pairs
```

See `evaluate.py`'s docstring for the fixture set and options.

## Linting and formatting

Pre-commit hooks run automatically on every commit:

- **ruff check --fix**
- **ruff format**
