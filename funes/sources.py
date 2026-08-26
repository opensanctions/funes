"""Read input CSV datasets into typed rows."""

import csv
import os

import fsspec
from pydantic import BaseModel


class InputRow(BaseModel):
    """One row of the input CSV: a URL plus its known metadata."""

    organization: str
    url: str


def load_inputs(base_path: str) -> list[tuple[str, list[InputRow]]]:
    """Load CSV files as ``(dataset, rows)`` pairs from an fsspec path.

    Each file stem names a dataset, and rows with blank URLs are omitted.
    """
    fs, base = fsspec.core.url_to_fs(base_path)
    result: list[tuple[str, list[InputRow]]] = []
    for path in sorted(fs.glob(os.path.join(base, "*.csv"))):
        with fs.open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = [
                InputRow(
                    organization=row["organization"].strip(),
                    url=row["url"].strip(),
                )
                for row in reader
                if row["url"].strip()
            ]
        # Each CSV is its own dataset, named after the file's stem.
        result.append((os.path.splitext(os.path.basename(path))[0], rows))
    return result
