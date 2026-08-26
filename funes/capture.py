"""Pravda integration and artifact reading.

- ``read_artifact`` reads a snapshot artifact blob from the shared fsspec
  storage backend Funes and Pravda both use.

Pravda is an in-process async library. Funes constructs a ``Pravda``
instance from its environment-backed settings for each capture; it never
speaks HTTP to Pravda.
"""

import fsspec
from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
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
