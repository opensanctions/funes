# Funes

Funes turns the internet into lists of politicians. It orchestrates web capture (via the in-process [Pravda](https://github.com/opensanctions/pravda) async library), LLM extraction, and structured storage to pull political position holders out of web pages.

## Status

Early R&D. Currently exploring what a viable automated extraction pipeline looks like.

## What it does

1. Captures snapshots (plaintext + rendered HTML + screenshot) via the in-process Pravda library against a remote browser, Postgres, and an artifact store that Funes owns and runs
2. Feeds snapshots to an LLM to extract structured "human / position" pairs
3. Persists each completed extraction with nested extraction-scoped persons and their positions, linked to Pravda snapshot identifiers
4. Writes each extraction's agent session as a JSONL transcript under `SESSIONS_BASE_PATH`

## Setup

Requires uv. Funes embeds Pravda as an async library and
owns the infrastructure Pravda connects to: a headed Chrome browser, a
Postgres database, and an artifact store. Pravda ships on PyPI as
`opensanctions-pravda` (imported as `pravda`); `uv sync` installs it.

```bash
# Install dependencies
uv sync
```

Bring up the shared infrastructure (Postgres and the headed Chrome that
Pravda drives) with:

```bash
docker compose up -d
```

## Migrations

Migrations are a **release step, not an app-startup step**: run `funes
migrate` once per deploy, before starting any `funes enqueue` or
`funes worker` process.

```bash
uv run funes migrate
```

`funes migrate` applies two independent Alembic ledgers to the shared
Postgres database, then runs an **append-only import**: URL/organization
rows from the configured input CSVs that are not yet in the page catalogue
are inserted. Missing associations may be added to an existing page, but
existing records are never updated or deleted.

- **Pravda's own ledger** ships with the `opensanctions-pravda` package and
  is applied as-is.
- **Funes's ledger** (`funes/migrations/`) tracks Funes's tables *and* the
  Procrastinate job-queue schema. Procrastinate ships its full schema and
  versioned upgrade SQL files, but no migration ledger or runner to track
  what has been applied, so Funes's Alembic tracks it on Procrastinate's
  behalf. Revision `0002` installs the initial schema for the pinned
  Procrastinate version (3.9.0) from an immutable vendored copy
  (`funes/migrations/sql/procrastinate_schema_3.9.0.sql`, byte-identical to
  `procrastinate/sql/schema.sql` at pin time). Bumping Procrastinate means
  vendoring the relevant upstream upgrade SQL for that release in a new
  Funes revision; historical revisions never read the installed package.

## Page catalogue

All pages are equal regardless of origin. A Page has a URL and may have
zero or more organization associations; organization associations come
from the input CSVs during `funes migrate`, and future agent-proposed
pages need not have an organization. The catalogue is append-only —
migration never updates or removes existing records.

## Usage

All commands run through the `funes` console script (installed by `uv sync`).

```bash
uv run funes migrate                   # apply schemas, then append-only import of new URL/organization associations
uv run funes enqueue                  # queue one job per persisted page
uv run funes worker                   # run a worker that executes queued jobs
```

`funes enqueue` reads persisted pages from the catalogue and queues exactly
one job per page, passing only its `page_id`; it does not create extraction
rows. `funes worker` executes queued jobs with its configured model and
persists a completed Extraction with its result for each page. Queued,
running, and failed work is tracked by [Procrastinate](https://procrastinate.readthedocs.io/),
a Postgres-backed job queue that reuses the same database (no new
environment variables — it connects through `PRAVDA_DATABASE_URL`).
