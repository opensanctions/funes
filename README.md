# Funes

Funes turns the internet into lists of politicians. It orchestrates web capture (via the in-process [Pravda](https://github.com/opensanctions/pravda) async library), LLM extraction, and structured storage to pull political position holders out of web pages.

## Status

Early R&D. Currently exploring what a viable automated extraction pipeline looks like.

## What it does

1. Captures snapshots (plaintext + rendered HTML + screenshot) via the in-process Pravda library against a remote browser, Postgres, and an artifact store that Funes owns and runs
2. Feeds snapshots to an LLM to extract structured "human / position" pairs
3. Stores extraction runs in PostgreSQL, linked to Pravda snapshot identifiers; each successful extraction stores nested extraction-scoped persons and their positions

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
migrate` once per deploy, before starting any `funes run`, `funes enqueue`,
or `funes worker` process.

```bash
uv run funes migrate
```

`funes migrate` applies two independent Alembic ledgers to the shared
Postgres database:

- **Pravda's own ledger** ships with the `opensanctions-pravda` package and
  is applied as-is.
- **Funes's ledger** (`funes/migrations/`) tracks Funes's tables *and* the
  Procrastinate job-queue schema. Procrastinate ships no migration ledger
  of its own — only a full-schema SQL file — so Funes's Alembic tracks it on
  Procrastinate's behalf. Revision `0002` installs the exact schema for the
  pinned Procrastinate version from an immutable vendored copy
  (`funes/migrations/sql/procrastinate_schema_3.9.0.sql`, byte-identical to
  `procrastinate/sql/schema.sql` at pin time). Bumping Procrastinate means
  vendoring the new schema file and adding a new Funes revision; historical
  revisions never read the installed package.

## Usage

All commands run through the `funes` console script (installed by `uv sync`). The input
location is an fsspec URL set via `INPUT_BASE_PATH` in `.env`
(a local dir or a `gs://`/`s3://` bucket prefix):

```bash
# Capture every URL from every CSV in the input directory (INPUT_BASE_PATH),
# extract position holders, and store them in PostgreSQL. Each CSV is its own
# dataset, named after the file's stem.
uv run funes run
uv run funes run -d hio_leadership   # one dataset only
uv run funes run -n 20               # random sample of 20 page inputs
uv run funes run -c 10               # up to 10 concurrent captures
```

Work is queued through [Procrastinate](https://procrastinate.readthedocs.io/),
a Postgres-backed job queue that reuses the same database (no new
environment variables — it connects through `PRAVDA_DATABASE_URL`):

```bash
uv run funes enqueue  # defer capture/extraction jobs from the input CSVs
uv run funes worker   # run a worker that executes queued jobs
```

## Evaluation

Score the extraction pipeline against hand-authored fixture pages. Each fixture is a directory under `fixtures/` holding `page.html`, an `expected.json` answer key, and optional `screenshot.png` and `url.txt` inputs. The harness derives page text and metadata, runs the real `extract()`, and exact-scores the returned holding observations. See `evaluate.py`'s docstring for details.

```bash
uv run python evaluate.py   # run all fixtures
uv run python evaluate.py -v # show expected pairs
```
