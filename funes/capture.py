"""Pravda capture integration and shared artifact storage access."""

import fsspec
from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
from pravda import Pravda, PravdaConfig

from funes.config import PravdaSettings


def pravda_client(settings: PravdaSettings) -> Pravda:
    """Construct a Pravda client from Funes's settings."""
    config = PravdaConfig(
        database_url=settings.database_url,
        browser_ws_url=settings.browser_ws_url,
        storage_base_path=settings.storage_base_path,
    )
    return Pravda(config)


def storage_filesystem(settings: PravdaSettings):
    """Return the async fsspec backend shared with Pravda.

    Synchronous backends are wrapped so all artifact reads stay on the current
    event loop. Using fsspec's sync bridge can move an async backend to its
    background loop after Pravda has bound the backend's session here.
    """
    fs, _ = fsspec.core.url_to_fs(settings.storage_base_path)
    if not fs.async_impl:
        fs = AsyncFileSystemWrapper(fs)
    return fs


async def read_artifact(fs, path: str) -> bytes:
    """Read a resolved artifact path through fsspec's asynchronous API."""
    return await fs._cat_file(path)
