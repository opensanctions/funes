# Funes

Funes turns the internet into lists of politicians. It orchestrates web capture (via the in-process [Pravda](https://github.com/opensanctions/pravda) async library), LLM extraction, and structured storage to pull political position holders out of web pages.

## Status

Early R&D. Currently exploring what a viable automated extraction pipeline looks like.

## What it does

1. Captures snapshots (plaintext + rendered HTML + screenshot) via the in-process Pravda library against a remote browser, Postgres, and an artifact store that Funes owns and runs
2. Feeds snapshots to an LLM to extract structured "human / position" pairs
3. Persists each completed extraction with nested extraction-scoped persons and their positions, linked to Pravda snapshot identifiers
4. Writes each agent run's session as one `.json` JSON-array transcript under `SESSIONS_BASE_PATH`

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
Postgres database, then bootstraps the page catalogue from the configured
input CSVs. The import is append-only: it creates missing pages and
URL/organization associations, but never updates or deletes existing records.
Running it again with the same inputs has no effect.

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

A Page is a durable crawl target identified by a unique URL and may have zero
or more organization associations. The CSV import is only a bootstrap
mechanism: Funes stores no source or provenance distinction, and pages later
proposed by agents will be stored exactly like imported pages. A proposed page
need not have a known organization.

## Usage

All commands run through the `funes` console script (installed by `uv sync`).

```bash
uv run funes migrate  # apply schemas and import missing pages
uv run funes enqueue  # queue one job per persisted page
uv run funes worker   # execute queued jobs
```

For now, each `funes enqueue` invocation selects every persisted page and
queues one job per page. The job payload contains only `page_id`; enqueue does
not create extraction rows. The worker resolves the Page, uses the model from
its own configuration, and persists an Extraction and its nested result only
after successful capture and extraction. Re-enqueuing a Page can therefore
produce another historical Extraction for it. Pages whose snapshot is
non-inspectable, or whose model output is a `BrokenPage`, get no Extraction
row; instead the pipeline defers one review job
(`funes.review_broken_page`, carrying page, snapshot, run, and reason) to a
separate `review` queue that the normal `funes worker` does not consume, so
those jobs sit pending until review is implemented.

Queued, running, and failed work is tracked by
[Procrastinate](https://procrastinate.readthedocs.io/), a Postgres-backed job
queue that reuses `PRAVDA_DATABASE_URL`. A failed job does not leave an
incomplete Extraction row.
