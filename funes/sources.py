"""Read input CSV files into dataset/objective/url rows."""

import csv
import os

import fsspec

REQUIRED_COLUMNS = ("objective", "url")


def load_inputs(base_path: str) -> list[tuple[str, str, str]]:
    """Load ``(dataset, objective, url)`` rows from every CSV under an fsspec path.

    ``dataset`` is the CSV filename without extension (e.g. ``hio_leadership``)
    and groups the file's pages under a future Zavod dataset. Each CSV must
    have exactly the columns ``objective,url``, and every row must have both
    values non-blank after stripping.
    """
    fs, base = fsspec.core.url_to_fs(base_path)
    rows: list[tuple[str, str, str]] = []
    for path in sorted(fs.glob(os.path.join(base, "*.csv"))):
        dataset = os.path.splitext(os.path.basename(path))[0]
        with fs.open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if tuple(reader.fieldnames or []) != REQUIRED_COLUMNS:
                raise ValueError(
                    f"{path}: expected columns exactly {list(REQUIRED_COLUMNS)}, "
                    f"got {list(reader.fieldnames or [])}"
                )
            for i, row in enumerate(reader, start=2):
                if any(row[c] is None for c in REQUIRED_COLUMNS):
                    raise ValueError(
                        f"{path}:{i}: row must have values for "
                        f"{', '.join(REQUIRED_COLUMNS)}"
                    )
                if None in row:
                    raise ValueError(
                        f"{path}:{i}: row has more values than columns "
                        f"({list(REQUIRED_COLUMNS)})"
                    )
                objective = row["objective"].strip()
                url = row["url"].strip()
                if not objective:
                    raise ValueError(f"{path}:{i}: objective must not be blank")
                if not url:
                    raise ValueError(f"{path}:{i}: url must not be blank")
                rows.append((dataset, objective, url))
    return rows
