"""Read input CSV files into dataset/objective/url rows."""

import csv
import os

import fsspec


def load_inputs(base_path: str) -> list[tuple[str, str, str]]:
    """Load ``(dataset, objective, url)`` rows from every CSV under an fsspec path.

    ``dataset`` is the CSV filename without extension (e.g. ``hio_leadership``)
    and groups the file's pages under a future Zavod dataset. Each CSV has the
    columns ``objective,url``.
    """
    fs, base = fsspec.core.url_to_fs(base_path)
    rows: list[tuple[str, str, str]] = []
    for path in sorted(fs.glob(os.path.join(base, "*.csv"))):
        dataset = os.path.splitext(os.path.basename(path))[0]
        with fs.open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows.extend(
                (
                    dataset,
                    row["objective"].strip(),
                    row["url"].strip(),
                )
                for row in reader
            )
    return rows
