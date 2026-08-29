# Funes

Funes turns web pages into inspection-brief-scoped people/position facts. It embeds [Pravda](https://github.com/opensanctions/pravda) for page capture, queues inspection jobs through Procrastinate, and extracts structured facts with a pydantic-ai agent.

Early R&D.

## How it works

YAML seed files define datasets of subjects and their URLs; each subject–URL pair is a candidate. A worker job captures a snapshot of the candidate's page, then an LLM judges the page against the dataset's brief: a **hit** stores the extracted people and positions, a **miss** records why nothing matched, and a broken snapshot is routed aside for repair. Hits are revisited after an interval; misses are not retried.

## Setup

Requires uv. Copy `.env.example` to `.env`, then:

```bash
docker compose up -d  # Postgres + headed Chrome
uv sync
```

## Usage

```bash
uv run --env-file .env funes migrate                    # apply schemas, import YAML seed catalogue
uv run --env-file .env funes enqueue                    # queue one job per due candidate
uv run --env-file .env procrastinate worker --queues inspect
```

`migrate` is append-only — re-running it imports only new catalogue entries. `enqueue` skips candidates that already have a pending job. Queue state is inspected with Procrastinate's own tooling, e.g. `uv run --env-file .env procrastinate shell list_jobs`.

## Evals

The extraction eval suite runs against frozen HTML fixtures with a real model — it costs tokens, so run it deliberately:

```bash
uv run --env-file .env python -m evals.run [--dataset PATH] [--model NAME] [--case NAME ...]
```

## Tests

```bash
uv run --env-file .env pytest
```
