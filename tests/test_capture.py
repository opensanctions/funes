"""Tests for the snapshot inspectability boundary."""

import asyncio
import uuid
from datetime import UTC, datetime

import fsspec
from pravda import Snapshot

from funes.capture import (
    artifact_filesystem,
    first_error_line,
    inspectability_issue,
    read_artifact,
)
from funes.config import PravdaSettings

NOW = datetime(2025, 1, 1, tzinfo=UTC)


def test_first_error_line():
    assert first_error_line(None) is None
    assert first_error_line("\n \n") is None
    assert first_error_line("TimeoutError: x") == "TimeoutError: x"
    # Playwright errors prefix a blank line before the first log line.
    assert first_error_line("\nTimeoutError: x\nsecond line") == "TimeoutError: x"


def make_settings(storage_base_path: str) -> PravdaSettings:
    return PravdaSettings(
        database_url="postgresql+asyncpg://funes:funes@localhost/funes",
        browser_ws_url="ws://localhost:3000",
        storage_base_path=storage_base_path,
    )


def test_read_artifact_reads_bytes_through_async_fs():
    base = f"/funes-{uuid.uuid4().hex}"
    fs = artifact_filesystem(make_settings(f"memory://{base}"))
    fsspec.filesystem("memory").pipe_file(f"{base}/page.html", b"<html>rendered</html>")

    assert (
        asyncio.run(read_artifact(fs, f"{base}/page.html")) == b"<html>rendered</html>"
    )


def make_snapshot(**overrides) -> Snapshot:
    fields = {
        "id": uuid.uuid4(),
        "url": "https://example.org/",
        "final_url": "https://example.org/",
        "captured_at": NOW,
        "http_status": 200,
        "error": None,
        "plaintext": "storage/example.org/plain.txt",
        "rendered_html": "storage/example.org/page.html",
        "screenshot": None,
        "http_archive": None,
    }
    fields.update(overrides)
    return Snapshot(**fields)


def test_complete_200() -> None:
    assert inspectability_issue(make_snapshot()) is None


def test_complete_403() -> None:
    assert inspectability_issue(make_snapshot(http_status=403)) is None


def test_complete_408() -> None:
    assert inspectability_issue(make_snapshot(http_status=408)) is None


def test_capture_error_with_artifacts() -> None:
    assert inspectability_issue(make_snapshot(error="timeout after capture")) is None


def test_navigation_error_no_artifacts() -> None:
    issue = inspectability_issue(
        make_snapshot(
            error="net::ERR_NAME_NOT_RESOLVED",
            http_status=None,
            plaintext=None,
            rendered_html=None,
        )
    )
    assert issue is not None
    assert "rendered_html" in issue
    assert "ERR_NAME_NOT_RESOLVED" in issue


def test_no_plaintext_is_inspectable() -> None:
    assert inspectability_issue(make_snapshot(plaintext=None)) is None


def test_missing_rendered_html_only() -> None:
    issue = inspectability_issue(make_snapshot(rendered_html=None))
    assert issue == "missing artifact(s): rendered_html"


def test_missing_both_artifacts_no_diagnostics() -> None:
    issue = inspectability_issue(
        make_snapshot(plaintext=None, rendered_html=None, http_status=None)
    )
    assert issue == "missing artifact(s): rendered_html"


def test_bad_final_url_scheme() -> None:
    issue = inspectability_issue(make_snapshot(final_url="file:///etc/passwd"))
    assert issue is not None
    assert "final URL" in issue


def test_relative_final_url() -> None:
    issue = inspectability_issue(make_snapshot(final_url="/about/"))
    assert issue is not None
    assert "/about/" in issue


def test_none_final_url() -> None:
    issue = inspectability_issue(
        make_snapshot(
            final_url=None,
            error="navigation aborted",
            plaintext=None,
            rendered_html=None,
        )
    )
    assert issue is not None
    assert issue.startswith("no final URL")
    assert "navigation aborted" in issue


def test_optional_screenshot_and_har() -> None:
    snapshot = make_snapshot(
        screenshot="storage/example.org/shot.png",
        http_archive={"log": {"entries": []}},
    )
    assert inspectability_issue(snapshot) is None


def test_multiline_error_truncated_to_first_line() -> None:
    error = 'TimeoutError: Timeout 30000ms exceeded.\n=========================== logs ===========================\nnavigating to "https://example.org/", waiting until "load"\n============================================================'
    issue = inspectability_issue(make_snapshot(error=error, rendered_html=None))
    assert (
        issue
        == "missing artifact(s): rendered_html (TimeoutError: Timeout 30000ms exceeded.)"
    )
