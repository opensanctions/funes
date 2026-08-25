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

The `run` command applies both Pravda's and Funes's packaged Alembic migrations idempotently before use, so no separate migration step is needed.

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

## Evaluation

Score the extraction pipeline against hand-authored fixture pages. Each fixture is a directory under `fixtures/` holding `page.html`, an `expected.json` answer key, and optional `screenshot.png` and `url.txt` inputs. The harness derives page text and metadata, runs the real `extract()`, and exact-scores the returned holding observations. See `evaluate.py`'s docstring for details.

```bash
uv run python evaluate.py   # run all fixtures
uv run python evaluate.py -v # show expected pairs
```
