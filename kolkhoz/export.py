"""Write one run's extracted holders as per-dataset JSONL files."""

import json
import logging
import os
from datetime import datetime

import fsspec
from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
from pravda import Snapshot

from kolkhoz.config import PathsConfig

log = logging.getLogger("kolkhoz")

# The flat JSONL schema handed to zavod. One record per (person, position)
# observation extracted from one snapshot. Fixed key order, UTF-8, source
# wording preserved (no FtM normalization — that is zavod's job).
EXPORT_FIELDS = [
    "dataset",
    "source_url",
    "snapshot_id",
    "snapshot_retrieved_at",
    "organisation_name",
    "person_name",
    "person_dob",
    "person_bio",
    "person_countries",
    "position_name",
    "position_organization",
    "position_description",
    "position_jurisdiction",
    "position_start_date",
    "position_end_date",
    "evidence_quotes",
]

EXPORT_DATE_FORMAT = "%Y-%m-%d"


def holder_to_record(
    dataset: str,
    source_url: str,
    organization: str,
    snapshot: Snapshot,
    holder: dict,
) -> dict:
    """Add input and snapshot provenance to one flattened holder."""
    record = {
        "dataset": dataset,
        "source_url": source_url,
        "snapshot_id": str(snapshot.id),
        "snapshot_retrieved_at": snapshot.captured_at.isoformat(),
        "organisation_name": organization,
        **holder,
    }
    return {name: record[name] for name in EXPORT_FIELDS}


async def write_outputs(groups: dict[str, list[dict]], paths: PathsConfig) -> None:
    """Write exactly the records produced by the current run.

    Runs inside the pipeline's event loop, so it uses the async fsspec API on
    that loop rather than the synchronous ``fs.makedirs``/``fs.open`` bridge;
    see ``kolkhoz.capture.storage_filesystem`` for why mixing the sync bridge
    with Pravda's async use of the shared (e.g. ``gcsfs``) instance raises
    "got Future attached to a different loop". Synchronous backends (local
    filesystem) are wrapped so the same async calls work there too.
    """
    fs, base = fsspec.core.url_to_fs(paths.output_base_path)
    if not fs.async_impl:
        fs = AsyncFileSystemWrapper(fs)
    date = datetime.now().strftime(EXPORT_DATE_FORMAT)
    total = 0
    for dataset, records in groups.items():
        out_dir = os.path.join(base, dataset)
        out_file = os.path.join(out_dir, f"{date}.jsonl")
        await fs._makedirs(out_dir, exist_ok=True)
        payload = b"".join(
            json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n"
            for record in records
        )
        await fs._pipe_file(out_file, payload)
        total += len(records)
        log.info("wrote %d record(s) → %s", len(records), out_file)
    log.info("wrote %d record(s) across %d dataset(s)", total, len(groups))
