# Funes

Funes is an orchestrator that turns raw web pages into structured data about political position holders. It is built on top of Pravda, the evidence layer that captures and stores durable snapshots of web pages.

## Project philosophy

- Early-stage. No backward compatibility. No fallback behaviors. Fail loud: no `try/except` unless there's a specific reason. We want errors to surface immediately.
- Development data is disposable. Prefer clean, destructive schema changes over compatibility migrations; databases and artifacts can be rebuilt when needed.

## Stack

- **Python** 3.13+ managed by **uv**.
- **Pravda** ([github.com/opensanctions/pravda](https://github.com/opensanctions/pravda)), published on PyPI as `opensanctions-pravda` (imported as `pravda`), for web page capture and storage, embedded as an in-process async library. Funes owns the infrastructure Pravda connects to — a headed Chrome browser (remote Playwright server), an async Postgres database, and an fsspec artifact store. Connection settings are `PRAVDA_DATABASE_URL`, `PRAVDA_BROWSER_WS_URL`, and `PRAVDA_STORAGE_BASE_PATH` (see `.env`). Funes constructs Pravda's `PravdaConfig` from worker configuration, reads artifacts from the shared storage backend over fsspec, and applies Pravda's packaged migrations (`pravda.migrate`) through the explicit `funes migrate` release command.
- Development infrastructure is shared. Do not create ad-hoc databases or browsers for tests.
- Funes queues page-processing jobs through Procrastinate, which is the ledger for queued, running, and failed work. A worker creates an Extraction only after successful capture and extraction, with nested extraction-scoped persons and positions in the PostgreSQL database shared with Pravda.

## Page catalogue lifecycle

- A Page is a durable crawl target with a unique URL and zero or more organization associations. There is no source or provenance distinction between CSV-imported pages and pages that agents may propose later.
- `funes migrate` applies the Pravda and Funes schemas, then bootstraps the catalogue from configured CSVs. This import creates missing pages and associations but never updates or deletes existing records.
- For now, each `funes enqueue` invocation selects every persisted Page and queues one job per page. The payload contains only `page_id`; enqueue creates no Extraction rows.
- `funes worker` resolves the Page, uses its own configured model, and persists a completed Extraction and result only on success. Procrastinate retains pending/running/failure state; repeated jobs can create extraction history for a Page.

## Project structure

```
funes/           # the package: cli.py (migrate, enqueue, worker), tasks.py
                   # (Procrastinate app + page task), capture.py (Pravda
                   # client + artifact helpers), extract.py, db.py, sources.py,
                   # config.py
```

`INPUT_BASE_PATH` in `.env` is an fsspec path to the bootstrap CSVs. Each CSV
has `organization,url` columns; rows with blank URLs are ignored, and a blank
organization creates a Page without an organization association.

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

## Linting and formatting

Pre-commit hooks run automatically on every commit:

- **ruff check --fix**
- **ruff format**
