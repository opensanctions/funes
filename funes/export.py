"""Export the latest successful database extractions as per-dataset JSONL."""

import json
import logging
import os
from datetime import UTC, datetime

import fsspec
from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from funes.config import PathsConfig
from funes.db import EXTRACTION_SUCCEEDED, Extraction, Holder, Page

log = logging.getLogger("funes")

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
]

EXPORT_DATE_FORMAT = "%Y-%m-%d"


def holder_to_record(page: Page, extraction: Extraction, holder: Holder) -> dict:
    """Flatten one stored holder and its provenance into an export record."""
    if extraction.snapshot_id is None or extraction.captured_at is None:
        raise ValueError(f"successful extraction {extraction.id} lacks a snapshot")
    record = {
        "dataset": page.dataset,
        "source_url": extraction.url,
        "snapshot_id": str(extraction.snapshot_id),
        "snapshot_retrieved_at": extraction.captured_at.isoformat(),
        "organisation_name": page.organization,
        "person_name": holder.person_name,
        "person_dob": holder.person_dob,
        "person_bio": holder.person_bio,
        "person_countries": holder.person_countries,
        "position_name": holder.position_name,
        "position_organization": holder.position_organization,
        "position_description": holder.position_description,
        "position_jurisdiction": holder.position_jurisdiction,
        "position_start_date": holder.position_start_date,
        "position_end_date": holder.position_end_date,
    }
    return {name: record[name] for name in EXPORT_FIELDS}


async def run_export(engine: AsyncEngine, paths: PathsConfig) -> None:
    """Export the latest successful extraction for each dataset and URL."""
    ranked = (
        select(
            Page.id.label("page_id"),
            Extraction.id.label("extraction_id"),
            func.row_number()
            .over(
                partition_by=(Page.dataset, Extraction.url),
                order_by=(Extraction.extracted_at.desc(), Extraction.id.desc()),
            )
            .label("rank"),
        )
        .join(Extraction, Page.extraction_id == Extraction.id)
        .where(Extraction.status == EXTRACTION_SUCCEEDED)
        .subquery()
    )
    statement = (
        select(Page, Extraction, Holder)
        .join(ranked, Page.id == ranked.c.page_id)
        .join(Extraction, Extraction.id == ranked.c.extraction_id)
        .join(Holder, Holder.extraction_id == Extraction.id)
        .where(ranked.c.rank == 1)
        .order_by(Page.dataset, Extraction.url, Holder.id)
    )

    groups: dict[str, list[dict]] = {}
    async with AsyncSession(engine) as session:
        rows = await session.execute(statement)
        for page, extraction, holder in rows.all():
            groups.setdefault(page.dataset, []).append(
                holder_to_record(page, extraction, holder)
            )

    await write_outputs(groups, paths)


async def write_outputs(groups: dict[str, list[dict]], paths: PathsConfig) -> None:
    """Write grouped export records to dated per-dataset JSONL files."""
    fs, base = fsspec.core.url_to_fs(paths.output_base_path)
    if not fs.async_impl:
        # Consistently use async API on pipeline's event loop
        fs = AsyncFileSystemWrapper(fs)
    date = datetime.now(UTC).strftime(EXPORT_DATE_FORMAT)
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
