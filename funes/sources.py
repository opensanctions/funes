"""Read input CSV files into URL/organization pairs."""

import csv
import os

import fsspec


def load_inputs(base_path: str) -> list[tuple[str, str]]:
    """Load ``(url, organization)`` pairs from every CSV under an fsspec path.

    Rows with blank URLs are omitted.
    """
    fs, base = fsspec.core.url_to_fs(base_path)
    rows: list[tuple[str, str]] = []
    for path in sorted(fs.glob(os.path.join(base, "*.csv"))):
        with fs.open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows.extend(
                (
                    row["url"].strip(),
                    row["organization"].strip(),
                )
                for row in reader
                if row["url"].strip()
            )
    return rows
