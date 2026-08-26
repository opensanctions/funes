"""Tests for the snapshot inspectability boundary."""

import uuid
from datetime import UTC, datetime

from pravda.snapshots import Snapshot

from funes.capture import inspectability_issue

NOW = datetime(2025, 1, 1, tzinfo=UTC)


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
    assert "plaintext" in issue and "rendered_html" in issue
    assert "ERR_NAME_NOT_RESOLVED" in issue


def test_missing_plaintext_only() -> None:
    issue = inspectability_issue(make_snapshot(plaintext=None))
    assert issue == "missing artifact(s): plaintext"


def test_missing_rendered_html_only() -> None:
    issue = inspectability_issue(make_snapshot(rendered_html=None))
    assert issue == "missing artifact(s): rendered_html"


def test_missing_both_artifacts_no_diagnostics() -> None:
    issue = inspectability_issue(
        make_snapshot(plaintext=None, rendered_html=None, http_status=None)
    )
    assert issue == "missing artifact(s): plaintext, rendered_html"


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
