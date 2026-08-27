"""Read input CSV files into dataset/URL/organization rows."""

import csv
import os

import fsspec


def load_inputs(base_path: str) -> list[tuple[str, str, str]]:
    """Load ``(dataset, url, organization)`` rows from every CSV under an fsspec path.

    ``dataset`` is the CSV filename without extension (e.g. ``hio_leadership``)
    and groups the file's pages under a future Zavod dataset. Rows with blank
    URLs are omitted.
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
                    row["url"].strip(),
                    row["organization"].strip(),
                )
                for row in reader
                if row["url"].strip()
            )
    return rows
