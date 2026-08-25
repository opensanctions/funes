"""Pravda integration and screenshot artifacts.

- ``read_artifact`` reads a snapshot artifact blob from the shared fsspec
  storage backend Funes and Pravda both use.
- ``is_blank`` / ``split_image`` are image primitives over a screenshot blob
  (shared by capture and the extraction tiling path).

Pravda is an in-process async library. Funes constructs a ``Pravda``
instance from its environment-backed settings for each capture; it never
speaks HTTP to Pravda.
"""

import io

import fsspec
from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
from PIL import Image
from pravda import Pravda, PravdaConfig

from funes.config import PravdaSettings


def pravda_client(settings: PravdaSettings) -> Pravda:
    """Construct a Pravda instance from Funes's environment-backed settings.

    The ``PravdaConfig`` is built here, at the application boundary, from the
    explicit settings Funes owns; Funes holds no Pravda URL of its own.
    """
    config = PravdaConfig(
        database_url=settings.database_url,
        browser_ws_url=settings.browser_ws_url,
        storage_base_path=settings.storage_base_path,
    )
    return Pravda(config)


def storage_filesystem(settings: PravdaSettings):
    """The shared fsspec backend Pravda writes artifacts to.

    Pravda resolves each snapshot's artifact fields to full storage paths
    against this same base path, so catting the resolved path on this
    filesystem locates the artifact for
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


async def read_artifact(fs, path: str) -> bytes:
    """Read a snapshot artifact blob from the shared storage backend.

    Since Pravda 0.1.4, ``Snapshot.plaintext`` / ``rendered_html`` /
    ``screenshot`` are full storage paths resolved against the shared base
    path, so *path* is opened as-is. Callers skip snapshots whose artifact
    fields are missing; a path reaching here is expected to exist, and a
    missing file fails loud rather than returning empty bytes.

    Reads through the async API on the current event loop; see
    ``storage_filesystem`` for why the sync ``fs.open`` path is avoided.
    """
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
