"""Pravda capture integration and shared artifact storage access."""

from urllib.parse import urlparse

import fsspec
from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
from pravda import Pravda, PravdaConfig, Snapshot

from funes.config import PravdaSettings


def first_error_line(error: str | None) -> str | None:
    """Return the first non-empty line of a capture error, or None."""
    if error is None:
        return None
    return next(
        (line.strip() for line in error.splitlines() if line.strip()),
        None,
    )


def inspectability_issue(snapshot: Snapshot) -> str | None:
    """Return None if the snapshot is inspectable, else a concise reason.

    A snapshot is inspectable when its final URL is an absolute http(s)
    URL and the rendered_html artifact path is present. HTTP status and
    ``snapshot.error`` do not disqualify a snapshot when rendered_html
    exists; plaintext, screenshot, and HAR are optional.
    """
    final_url = snapshot.final_url
    if final_url is None:
        reason = "no final URL"
    else:
        parsed = urlparse(final_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            reason = f"final URL is not an absolute http(s) URL: {final_url!r}"
        else:
            reason = None
    if reason is None and not snapshot.rendered_html:
        reason = "missing artifact(s): rendered_html"
    if reason is None:
        return None
    if snapshot.error:
        # Playwright errors are multiline logs; keep only the first line.
        diagnostics = first_error_line(snapshot.error)
    elif snapshot.http_status is not None and snapshot.http_status >= 400:
        diagnostics = f"http status {snapshot.http_status}"
    else:
        diagnostics = None
    if diagnostics:
        return f"{reason} ({diagnostics})"
    return reason


def pravda_client(settings: PravdaSettings) -> Pravda:
    """Construct a Pravda client from Funes's settings."""
    config = PravdaConfig(
        database_url=settings.database_url,
        browser_ws_url=settings.browser_ws_url,
        storage_base_path=settings.storage_base_path,
    )
    return Pravda(config)


def artifact_filesystem(settings: PravdaSettings):
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
