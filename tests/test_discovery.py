"""Tests for page link enumeration, the link-selection schema, and the
discovery agent."""

import asyncio
import json
import re

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, RetryPromptPart, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from funes.agents import Brief
from funes.discovery import (
    Discovery,
    LinkSelection,
    discovery_agent,
    page_link_urls,
)
from funes.outline import build_outline

BASE = "https://example.org/about/"


# --- page link enumeration ---


def test_link_urls_resolved_deduplicated_in_set() -> None:
    html = """
    <body>
      <a href="/people/jane">Jane</a>
      <a href="bio.html">Bio</a>
      <a href="https://other.example.net/x">Elsewhere</a>
      <a href="/people/jane#top">Jane again</a>
      <a href="mailto:a@b.example">Mail</a>
    </body>
    """
    assert page_link_urls(BASE, html) == {
        "https://example.org/people/jane",
        "https://example.org/about/bio.html",
        "https://other.example.net/x",
    }


def test_link_urls_fragments_stripped_queries_preserved() -> None:
    html = '<body><a href="/search?q=jane&amp;page=2#results">Search</a></body>'
    assert page_link_urls(BASE, html) == {"https://example.org/search?q=jane&page=2"}


def test_link_urls_non_http_schemes_rejected() -> None:
    html = """
    <body>
      <a href="javascript:void(0)">JS</a>
      <a href="ftp://files.example.org/f">FTP</a>
      <a href="tel:+1234">Call</a>
      <a href="/kept">Kept</a>
    </body>
    """
    assert page_link_urls(BASE, html) == {"https://example.org/kept"}


def test_link_urls_non_http_final_url_fails_loud() -> None:
    with pytest.raises(ValueError):
        page_link_urls("page.html", "<body><a href='/x'>x</a></body>")


def test_outline_hrefs_are_selectable_link_urls() -> None:
    """Every href the outline shows the model passes selection validation.

    The discovery agent copies URLs from the outline, so the outline's
    href rendering and the validation set must agree — including on
    fragment stripping.
    """
    html = """
    <body>
      <h1>Board</h1>
      <a href="/people/jane#top">Jane</a>
      <a href="/search?q=board&amp;page=2#r">Board</a>
      <img src="/img/team.png#v2" alt="Team">
    </body>
    """
    hrefs = set(re.findall(r"\[href=([^\]]+)\]", build_outline(BASE, html)))
    assert hrefs == {
        "https://example.org/people/jane",
        "https://example.org/search?q=board&page=2",
    }
    assert hrefs <= page_link_urls(BASE, html)


# --- link-selection schema ---


def test_link_selection_requires_nonblank_url_and_reason():
    selection = LinkSelection(
        url="  https://example.org/board  ",
        reason="  Lists the foundation's board.  ",
    )
    assert selection.url == "https://example.org/board"
    assert selection.reason == "Lists the foundation's board."
    with pytest.raises(ValidationError):
        LinkSelection(url="https://example.org", reason="")
    with pytest.raises(ValidationError):
        LinkSelection(url="   ", reason="Lists the board.")
    with pytest.raises(ValidationError):
        LinkSelection(url="https://example.org")
    with pytest.raises(ValidationError):
        LinkSelection(url="https://example.org", reason="ok", unexpected=True)


def test_discovery_is_strict_without_length_limits():
    discovery = Discovery(
        selections=[
            LinkSelection(url="https://example.org/a", reason="Roster of members."),
            LinkSelection(url="https://example.org/b", reason="Leadership team page."),
        ]
    )
    assert [s.url for s in discovery.selections] == [
        "https://example.org/a",
        "https://example.org/b",
    ]
    # No minimum or maximum on the selection count.
    assert Discovery(selections=[]).selections == []
    many = Discovery(
        selections=[
            LinkSelection(url=f"https://example.org/{i}", reason=f"Reason {i}.")
            for i in range(50)
        ]
    )
    assert len(many.selections) == 50
    with pytest.raises(ValidationError):
        Discovery(selections=[], unexpected=True)


# --- discovery agent runs ---


_BRIEF = Brief(
    people_sought="Board members",
    subject_label="Organization",
    subject="Example Foundation",
)

_SELECTIONS = {
    "selections": [
        {
            "url": "https://example.org/board",
            "reason": "Likely the foundation's board roster.",
        },
        {
            "url": "https://example.org/about/leadership",
            "reason": "Leadership page may name current directors.",
        },
    ]
}


def test_discovery_agent_function_model_returns_selections():
    def fn(messages, info):
        return ModelResponse(parts=[TextPart(json.dumps(_SELECTIONS))])

    with discovery_agent.override(model=FunctionModel(fn)):
        result = asyncio.run(
            discovery_agent.run("<page_snapshot>ignored</page_snapshot>", deps=_BRIEF)
        )

    assert isinstance(result.output, Discovery)
    assert [s.url for s in result.output.selections] == [
        "https://example.org/board",
        "https://example.org/about/leadership",
    ]
    assert result.output.selections[0].reason == "Likely the foundation's board roster."


def test_discovery_agent_test_model_produces_valid_output():
    test_model = TestModel(
        profile={"supports_json_schema_output": True},
        custom_output_text=json.dumps(_SELECTIONS),
    )
    with discovery_agent.override(model=test_model):
        result = asyncio.run(
            discovery_agent.run("<page_snapshot>ignored</page_snapshot>", deps=_BRIEF)
        )

    assert isinstance(result.output, Discovery)
    assert len(result.output.selections) == 2
    assert isinstance(result.output.selections[0], LinkSelection)


def test_discovery_agent_function_model_retries_invalid_output():
    def fn(messages, info):
        if any(
            isinstance(p, RetryPromptPart)
            for m in messages
            for p in getattr(m, "parts", [])
        ):
            return ModelResponse(parts=[TextPart(json.dumps(_SELECTIONS))])
        return ModelResponse(
            parts=[TextPart(json.dumps({"selections": [{"url": ""}]}))]
        )

    with discovery_agent.override(model=FunctionModel(fn)):
        result = asyncio.run(
            discovery_agent.run("<page_snapshot>ignored</page_snapshot>", deps=_BRIEF)
        )

    assert isinstance(result.output, Discovery)
    retries = [
        p
        for m in result.all_messages()
        for p in getattr(m, "parts", [])
        if isinstance(p, RetryPromptPart)
    ]
    assert retries
