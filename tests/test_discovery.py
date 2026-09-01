"""Tests for candidate link enumeration, the link-selection schema, the
discovery agent, and its prompt."""

import asyncio
import json
import logging

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, RetryPromptPart, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from funes.agents import Brief
from funes.discovery import (
    CandidateLink,
    Discovery,
    LinkSelection,
    build_discovery_prompt,
    candidate_links,
    discovery_agent,
)

BASE = "https://example.org/about/"


# --- candidate link enumeration ---


def test_links_resolved_and_deduplicated_in_dom_order() -> None:
    html = """
    <body>
      <a href="/people/jane">Jane</a>
      <a href="bio.html">Bio</a>
      <a href="https://other.example.net/x">Elsewhere</a>
      <a href="/people/jane#top">Jane again</a>
      <a href="mailto:a@b.example">Mail</a>
    </body>
    """
    assert candidate_links(BASE, html) == [
        CandidateLink(url="https://example.org/people/jane", anchor="Jane"),
        CandidateLink(url="https://example.org/about/bio.html", anchor="Bio"),
        CandidateLink(url="https://other.example.net/x", anchor="Elsewhere"),
    ]


def test_links_fragments_stripped_queries_preserved() -> None:
    html = '<body><a href="/search?q=jane&amp;page=2#results">Search</a></body>'
    assert candidate_links(BASE, html) == [
        CandidateLink(url="https://example.org/search?q=jane&page=2", anchor="Search")
    ]


def test_links_anchor_text_normalized() -> None:
    html = """
    <body>
      <a href="/a">  Jane   Doe </a>
      <a href="/b"><img src="/x.png" alt="Ignored"></a>
      <a href="/c">   </a>
      <a href="/d"><span>Jane</span><span>Doe</span></a>
    </body>
    """
    assert candidate_links(BASE, html) == [
        CandidateLink(url="https://example.org/a", anchor="Jane Doe"),
        CandidateLink(url="https://example.org/b", anchor=None),
        CandidateLink(url="https://example.org/c", anchor=None),
        CandidateLink(url="https://example.org/d", anchor="Jane Doe"),
    ]


def test_links_non_http_schemes_rejected() -> None:
    html = """
    <body>
      <a href="javascript:void(0)">JS</a>
      <a href="ftp://files.example.org/f">FTP</a>
      <a href="tel:+1234">Call</a>
      <a href="/kept">Kept</a>
    </body>
    """
    assert candidate_links(BASE, html) == [
        CandidateLink(url="https://example.org/kept", anchor="Kept")
    ]


def test_links_capped_at_200_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    html = (
        "<body>"
        + "".join(f'<a href="/link/{i}">Link {i}</a>' for i in range(250))
        + "</body>"
    )
    with caplog.at_level(logging.WARNING, logger="funes.discovery"):
        links = candidate_links(BASE, html)
    assert len(links) == 200
    assert links[0] == CandidateLink(url="https://example.org/link/0", anchor="Link 0")
    assert any("capped" in record.message for record in caplog.records)


def test_links_non_http_final_url_fails_loud() -> None:
    with pytest.raises(ValueError):
        candidate_links("page.html", "<body><a href='/x'>x</a></body>")


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
            discovery_agent.run(
                "<candidate_links>ignored</candidate_links>", deps=_BRIEF
            )
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
            discovery_agent.run(
                "<candidate_links>ignored</candidate_links>", deps=_BRIEF
            )
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
            discovery_agent.run(
                "<candidate_links>ignored</candidate_links>", deps=_BRIEF
            )
        )

    assert isinstance(result.output, Discovery)
    retries = [
        p
        for m in result.all_messages()
        for p in getattr(m, "parts", [])
        if isinstance(p, RetryPromptPart)
    ]
    assert retries


# --- discovery prompt construction ---


def test_build_discovery_prompt_enumerates_urls_and_anchor_text():
    links = [
        CandidateLink(url="https://example.org/board", anchor="Board of Directors"),
        CandidateLink(url="https://example.org/team?ref=nav", anchor="Our Team"),
        CandidateLink(url="https://example.org/müller", anchor="Vorstand (Müller)"),
        CandidateLink(url="https://example.org/img-logo", anchor=None),
    ]
    prompt = build_discovery_prompt(links)
    assert prompt.startswith("<candidate_links>\n")
    assert prompt.endswith("\n</candidate_links>")
    assert "1. URL: https://example.org/board" in prompt
    assert 'Anchor text: "Board of Directors"' in prompt
    assert "2. URL: https://example.org/team?ref=nav" in prompt
    assert 'Anchor text: "Our Team"' in prompt
    assert "3. URL: https://example.org/müller" in prompt
    assert 'Anchor text: "Vorstand (Müller)"' in prompt
    assert "4. URL: https://example.org/img-logo" in prompt
    assert "Anchor text: null" in prompt
    # No page context beyond the links themselves.
    assert "<page_outline>" not in prompt
    assert "<page_snapshot>" not in prompt


def test_build_discovery_prompt_without_links():
    assert build_discovery_prompt([]) == "<candidate_links>\n</candidate_links>"
