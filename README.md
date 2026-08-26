# Funes

Funes turns the internet into lists of politicians. It orchestrates web capture (via the in-process [Pravda](https://github.com/opensanctions/pravda) library), LLM extraction, and structured storage to pull political position holders out of web pages.

## Status

Early R&D. Currently exploring what a viable automated extraction pipeline looks like.

## What it does

1. Captures snapshots (plaintext, rendered HTML, HAR, screenshot) via Pravda, against a remote browser, Postgres, and an artifact store that Funes owns and runs
2. Builds a compact outline of the rendered HTML and feeds it, with page metadata, to an LLM that extracts structured person/position pairs; the model can fetch page resources such as images through a tool
3. Persists each completed extraction with nested extraction-scoped persons and positions, linked to Pravda snapshot identifiers
4. Writes each agent run's message history as one JSON transcript under `SESSIONS_BASE_PATH`

Pages whose snapshot is non-inspectable, or whose model output is a `BrokenPage`, get no extraction; the job defers to a separate `review` queue that the normal worker does not consume, so those jobs sit pending until review is implemented.

## Setup

Requires uv. Copy `.env.example` to `.env`, then bring up the shared infrastructure (Postgres and the headed Chrome that Pravda drives) and install:

```bash
docker compose up -d
uv sync
```

## Usage

All commands run through the `funes` console script:

```bash
uv run funes migrate  # apply schemas and import missing pages
uv run funes enqueue  # queue one job per persisted page
uv run funes worker   # consume the process queue (-c/--concurrency N)
```

`migrate` is a release step, not an app-startup step: run it once per deploy, before starting `enqueue` or `worker`. It applies two Alembic ledgers to the shared Postgres database — Pravda's, shipped with the `opensanctions-pravda` package, and Funes's (`funes/migrations/`), which tracks Funes's tables plus the Procrastinate job-queue schema vendored at the pinned Procrastinate version. Bumping Procrastinate means vendoring its upgrade SQL in a new Funes revision. It then bootstraps the page catalogue from the CSVs under `INPUT_BASE_PATH` (columns `organization,url`); the import is append-only and never updates or deletes existing records.

`enqueue` queues one `page_id` job per persisted page and creates no extraction rows. `worker` resolves the page, captures a snapshot, runs extraction with the model from its own configuration, and commits the extraction graph only on success — a failed job leaves no incomplete extraction behind. Re-enqueuing a page produces another historical extraction. Queued, running, and failed jobs are tracked by [Procrastinate](https://procrastinate.readthedocs.io/) in the same Postgres database.

## Tests

```bash
uv run pytest
```
