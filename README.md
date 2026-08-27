# Funes

Funes turns web pages into objective-scoped people/position facts. It orchestrates web capture (via the in-process [Pravda](https://github.com/opensanctions/pravda) library), LLM extraction against natural-language objectives, and structured storage.

## Status

Early R&D. Currently exploring what a viable automated extraction pipeline looks like.

## Model

Inputs are CSV files with columns `objective,url`. Each file's stem names a **Dataset**. An **Objective** is natural language describing what to learn — e.g. `Heads of the Global Environment Facility` — and a **URL** is a global identity, deduplicated across objectives. A **Candidate** links one objective to one URL and is the pipeline's unit of work.

A worker run on a candidate captures an immutable Pravda **Snapshot** (plaintext, rendered HTML, HAR, screenshot) against a remote browser, Postgres, and an artifact store that Funes owns and runs. The run becomes an **Attempt** linking the candidate to the snapshot. Infra failures (exceptions) write no attempt; retrying them is Procrastinate's business, not a domain fact.

Every completed attempt records exactly one **SnapshotAssessment** — the judgement of the snapshot itself: `usable` or `broken`. Snapshot usability is objective-independent. Broken is explicit and routed to a dormant repair queue the normal worker does not consume; brokenness is never attached to a URL, and it produces no inspection.

A usable assessment additionally gets exactly one objective-relative **Inspection**: `hit` (the objective is satisfied; extracted persons and their positions are attached) or `miss` (nothing on the page satisfies the objective, with a reason). Hits are revisited after an interval; misses are not normally retried.

Extraction feeds a compact outline of the rendered HTML plus page metadata to an LLM, which can fetch page resources such as images through a tool. Each agent run's message history is written as one JSON transcript under `SESSIONS_BASE_PATH`.

A natural next direction is discovering new URLs worth adding as candidates; that is not implemented today.

## Setup

Requires uv. Copy `.env.example` to `.env`, then bring up the shared infrastructure (Postgres and the headed Chrome that Pravda drives) and install:

```bash
docker compose up -d
uv sync
```

## Usage

One-shot commands run through the `funes` console script; the worker is Procrastinate's own CLI. All commands run as `uv run --env-file .env …`, which injects `.env` into the process environment, and `PROCRASTINATE_APP` in `.env` points Procrastinate's CLI at the module-level app in `funes/procrastinate.py`:

```bash
uv run --env-file .env funes migrate  # apply schemas and import missing candidates
uv run --env-file .env funes enqueue  # queue one job per due candidate (revisit interval, deduped)
uv run --env-file .env procrastinate worker  # consume the process queue (-c/--concurrency N)
```

Queued, running, and failed jobs live in the same Postgres database and are inspected with Procrastinate's own tooling:

```bash
uv run --env-file .env procrastinate shell            # interactive: list_jobs, list_queues, cancel, retry
uv run --env-file .env procrastinate shell list_jobs  # one-shot
uv run --env-file .env procrastinate healthchecks     # configuration and DB sanity check
```

`migrate` applies two Alembic ledgers to the shared Postgres database — Pravda's, shipped with the `opensanctions-pravda` package, and Funes's (`funes/migrations/`), which tracks Funes's tables plus the Procrastinate job-queue schema vendored at the pinned Procrastinate version. Bumping Procrastinate means vendoring its upgrade SQL in a new Funes revision. It then bootstraps the catalogue from the CSVs under `INPUT_BASE_PATH` (columns `objective,url`): each file becomes a `dataset` row named after its filename stem, with objectives and global URLs linked through candidates. The import is append-only and never updates or deletes existing records.

`enqueue` queues one `inspect_candidate` job per due candidate: candidates never attempted, plus candidates whose latest attempt was a hit inspection older than `REVISIT_INTERVAL_DAYS`. A latest miss inspection or broken assessment blocks re-enqueueing; broken snapshots belong to the (unimplemented) repair path, not the normal queue. Each job is deferred with a per-candidate queueing lock, so a candidate that already has a pending job is skipped rather than double-queued.

## Tests

```bash
uv run --env-file .env pytest
```
