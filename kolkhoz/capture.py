"""Pravda integration and screenshot artifacts.

- ``capture_urls`` captures each URL once, concurrently, through one
  long-lived Pravda instance (bounded by a semaphore).
- ``read_artifact`` reads a snapshot artifact blob from the shared fsspec
  storage backend Kolkhoz and Pravda both use.
- ``is_blank`` / ``split_image`` are image primitives over a screenshot blob
  (shared by capture and the extraction tiling path).

Pravda is an in-process async library. Kolkhoz constructs one ``Pravda``
instance from its environment-backed settings and reuses it across captures;
it never speaks HTTP to Pravda.
"""

import asyncio
import io
import logging
import os
from collections import Counter
from dataclasses import dataclass

import fsspec
from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
from PIL import Image
from pravda import Pravda, PravdaConfig, Snapshot

from kolkhoz.config import PravdaSettings

log = logging.getLogger("kolkhoz")


def pravda_client(settings: PravdaSettings) -> Pravda:
    """Construct a Pravda instance from Kolkhoz's environment-backed settings.

    The ``PravdaConfig`` is built here, at the application boundary, from the
    explicit settings Kolkhoz owns; Kolkhoz holds no Pravda URL of its own.
    """
    config = PravdaConfig(
        database_url=settings.database_url,
        browser_ws_url=settings.browser_ws_url,
        storage_base_path=settings.storage_base_path,
    )
    return Pravda(config)


def storage_filesystem(settings: PravdaSettings):
    """The shared fsspec backend Pravda writes artifacts to.

    Pravda resolves each snapshot's ``prefix`` against this same base path, so
    opening ``<prefix>/<filename>`` on this filesystem locates the artifact for
    both local paths and remote (``gs://``/``s3://``) URLs.

    Synchronous backends (e.g. the local filesystem) are wrapped so reads use
    the same async API as remote ones. This mirrors Pravda's own
    ``Storage.from_url`` and, crucially, keeps every artifact read on the
    running event loop: reading via the async API avoids fsspec's sync bridge,
    which would otherwise drive the shared (async, e.g. ``gcsfs``) instance from
    its background loop while Pravda has bound its session to this loop —
    raising "got Future attached to a different loop".
    """
    fs, _ = fsspec.core.url_to_fs(settings.storage_base_path)
    if not fs.async_impl:
        fs = AsyncFileSystemWrapper(fs)
    return fs


async def read_artifact(fs, snapshot: Snapshot, filename: str | None) -> bytes:
    """Read a snapshot artifact blob from the shared storage backend.

    ``snapshot.prefix`` is the backend-resolved directory (base path plus the
    normalized host of ``final_url``); *filename* is the bare
    content-addressed name Pravda stored. Both are required for a stored
    artifact: a missing one is a malformed snapshot, so this fails loud rather
    than returning empty bytes.

    Reads through the async API on the current event loop; see
    ``storage_filesystem`` for why the sync ``fs.open`` path is avoided.
    """
    if snapshot.prefix is None:
        raise ValueError(f"snapshot {snapshot.id} has no storage prefix")
    if filename is None:
        raise ValueError(f"snapshot {snapshot.id} has no artifact filename")
    path = os.path.join(snapshot.prefix, filename)
    return await fs._cat_file(path)


def is_blank(blob: bytes) -> bool:
    """True if the image is a single solid colour (a blank or failed render).

    ``getcolors(1)`` returns a list iff the image has at most one distinct
    colour, else None — so a blank white/black/any-colour page reads as blank.
    """
    image = Image.open(io.BytesIO(blob))
    return image.getcolors(1) is not None


def split_image(blob: bytes, tile: int, overlap: float) -> list[bytes]:
    """Slice an image into *overlap*-fraction overlapping *tile*-px tall strips.

    Screenshots are hardclipped for width, so only the height axis ever needs
    slicing: each strip keeps the full width. Strips are laid out on a stride
    of ``tile * (1 - overlap)``, with a shorter remainder strip at the end if
    needed. Images no taller than *tile* come back as a single strip.

    Solid-colour strips (remainder offcuts, background bands) carry no
    content and are dropped via the same ``getcolors(1)`` check as
    ``is_blank``, so they never reach the model.
    """
    image = Image.open(io.BytesIO(blob))
    width, height = image.size

    def spans(size: int) -> list[tuple[int, int]]:
        if size <= tile:
            return [(0, size)]
        stride = round(tile * (1 - overlap))
        result: list[tuple[int, int]] = []
        start = 0
        while start + tile <= size:
            result.append((start, start + tile))
            start += stride
        if start < size:
            result.append((start, size))
        return result

    tiles: list[bytes] = []
    for top, bottom in spans(height):
        crop = image.crop((0, top, width, bottom))
        if crop.getcolors(1) is not None:
            continue
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        tiles.append(buf.getvalue())
    return tiles


@dataclass
class OperationalError:
    """A per-URL failure that must not abort the whole run.

    Distinct from the capture failures Pravda *persists* on a snapshot with
    ``error`` set (HTTP status, browser/context failures): those come back as
    an errored Snapshot and are recorded in the database. An
    ``OperationalError`` is one Pravda *raises* instead — e.g. an inner HAR
    processing or storage timeout, which Pravda's own code notes is an
    operational error rather than persisted evidence. We gather these, count
    them, and let the run finish; they belong in the log, not the database.
    """

    stage: str
    url: str
    error: str


def summarise_errors(errors: list[OperationalError]) -> None:
    """Log a summary of the operational errors gathered during the run."""
    if not errors:
        log.info("operational errors: none")
        return
    by_stage = Counter(e.stage for e in errors)
    by_type = Counter(e.error.split(":", 1)[0] for e in errors)
    log.warning(
        "operational errors: %d across %d URL(s) — by stage: %s",
        len(errors),
        len({e.url for e in errors}),
        ", ".join(f"{stage}={n}" for stage, n in by_stage.most_common()),
    )
    for etype, count in by_type.most_common():
        log.warning("  %s: %d", etype, count)
    for error in errors:
        log.warning("  [%s] %s — %s", error.stage, error.url, error.error)


async def capture_urls(
    pravda: Pravda, urls: list[str], concurrency: int
) -> tuple[dict[str, Snapshot], list[OperationalError]]:
    """Capture each URL once, concurrently, through one Pravda instance.

    At most *concurrency* captures run at once (an ``asyncio.Semaphore``
    bounds them); each is otherwise independent. Returns a mapping of the
    requested URL to the Snapshot Pravda persisted for it, plus a list of
    operational errors. Pravda persists capture failures with ``error`` set
    rather than raising, so those still appear in the mapping as errored
    snapshots. A URL that Pravda fails *operationally* (raising, e.g. a HAR
    timeout) is absent from the mapping and recorded as an ``OperationalError``
    instead, so one such failure cannot abort the whole run.
    """
    sem = asyncio.Semaphore(concurrency)
    captures: dict[str, Snapshot] = {}
    errors: list[OperationalError] = []

    async def snap(url: str) -> None:
        async with sem:
            try:
                snapshot = await pravda.snapshot(url)
            except Exception as exc:
                # Never abort the run for one URL. CancelledError /
                # KeyboardInterrupt are BaseException and still propagate.
                log.warning(
                    "capture failed operationally for %s: %s: %s",
                    url,
                    type(exc).__name__,
                    exc,
                )
                errors.append(
                    OperationalError("capture", url, f"{type(exc).__name__}: {exc}")
                )
                return
            log.info("snapshotted %s has_error=%s", snapshot.url, snapshot.error is not None)
            captures[url] = snapshot

    await asyncio.gather(*(snap(url) for url in urls))
    return captures, errors
