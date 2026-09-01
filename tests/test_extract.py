"""Tests for the extraction schema, prompts, tools, and result union."""

import asyncio
import json

import pytest
from pydantic import TypeAdapter, ValidationError
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.messages import (
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from funes.extract import (
    Brief,
    BrokenSnapshot,
    Discovery,
    ExtractionDependencies,
    Hit,
    LinkSelection,
    Miss,
    PageMetadata,
    PageResult,
    Person,
    Position,
    build_discovery_prompt,
    build_prompt,
    discovery_agent,
    extraction_agent,
    metadata_from_html,
    render_brief,
    view_resource,
)
from funes.outline import CandidateLink

result_adapter = TypeAdapter(PageResult)

_PERSON = {
    "name": "Amina Diallo",
    "countries": ["Senegal"],
    "positions": [
        {
            "name": "Director",
            "organization": "Example Foundation",
            "start_date": "2021",
        },
        {"name": "Chair", "organization": "Example Foundation"},
    ],
}


def _native_text(member: str, payload: dict) -> str:
    """Serialize one PageResult member in the native-output union envelope.

    Multi-type native output validates one combined schema: an outer
    ``result`` object whose ``kind`` names the member class and whose ``data``
    carries the member payload.
    """
    return json.dumps({"result": {"kind": member, "data": payload}})


# --- union discrimination and validation ---


def test_hit_requires_at_least_one_person():
    assert (
        Hit(persons=[{"name": "A", "positions": [{"name": "Director"}]}]).kind == "hit"
    )
    with pytest.raises(ValidationError):
        Hit(persons=[])
    with pytest.raises(ValidationError):
        Hit()
    with pytest.raises(ValidationError):
        Person(name="   ", positions=[Position(name="Director")])
    with pytest.raises(ValidationError):
        Position(name="   ")


def test_output_models_reject_unknown_fields():
    with pytest.raises(ValidationError):
        Miss(reason="no holders", unexpected=True)


def test_miss_requires_nonblank_reason():
    miss = Miss(reason="  No board members listed; page is a news article.  ")
    assert miss.kind == "miss"
    assert miss.reason == "No board members listed; page is a news article."
    with pytest.raises(ValidationError):
        Miss()
    with pytest.raises(ValidationError):
        Miss(reason="")
    with pytest.raises(ValidationError):
        Miss(reason="   ")


def test_broken_snapshot_requires_nonblank_reason():
    broken = BrokenSnapshot(reason="Cloudflare challenge")
    assert broken.kind == "broken"
    with pytest.raises(ValidationError):
        BrokenSnapshot()
    with pytest.raises(ValidationError):
        BrokenSnapshot(reason="")
    with pytest.raises(ValidationError):
        BrokenSnapshot(reason="   ")


def test_union_discriminates_on_kind():
    hit_payload = {
        "kind": "hit",
        "persons": [{"name": "Amina Diallo", "positions": [{"name": "Director"}]}],
    }
    assert isinstance(result_adapter.validate_python(hit_payload), Hit)
    assert isinstance(
        result_adapter.validate_python({"kind": "miss", "reason": "no holders"}), Miss
    )
    assert isinstance(
        result_adapter.validate_python({"kind": "broken", "reason": "bot challenge"}),
        BrokenSnapshot,
    )
    assert (
        result_adapter.validate_python(
            {"kind": "broken", "reason": " bot challenge "}
        ).reason
        == "bot challenge"
    )
    with pytest.raises(ValidationError):
        result_adapter.validate_python({"kind": "other"})
    with pytest.raises(ValidationError):
        result_adapter.validate_python({"kind": "hit"})


def test_union_rejects_payload_missing_kind():
    # Without kind, the discriminated union must fail loudly.
    with pytest.raises(ValidationError):
        result_adapter.validate_python({"reason": "404"})
    with pytest.raises(ValidationError):
        result_adapter.validate_python({"persons": []})


# --- view_resource tool ---


def _view_agent(function):
    """A lightweight agent exposing view_resource, driven by *function*.

    The first model turn returns whatever *function* decides; a final text
    response ends the run so we can inspect the tool traffic.
    """
    return Agent(
        FunctionModel(function),
        output_type=str,
        tools=[view_resource],
        deps_type=ExtractionDependencies,
    )


def _deps(media_types, data=b"fake-image-bytes"):
    async def read_resource(body_path: str) -> bytes:
        return data

    return ExtractionDependencies(
        brief=Brief(
            people_sought="Board members",
            subject_label="Organization",
            subject="Example Foundation",
        ),
        read_resource=read_resource,
        resource_media_types=media_types,
    )


def _tool_return_part(result):
    parts = [p for m in result.all_messages() for p in getattr(m, "parts", [])]
    return parts


def test_view_resource_returns_binary_content():
    def fn(messages, info):
        assert [t.name for t in info.function_tools] == ["view_resource"]
        if not any(
            getattr(p, "tool_name", None) == "view_resource"
            for m in messages
            for p in getattr(m, "parts", [])
        ):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="view_resource", args={"body_path": "bodies/a.jpg"}
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("done")])

    deps = _deps({"bodies/a.jpg": "image/jpeg"}, b"\xff\xd8jpeg")
    result = asyncio.run(_view_agent(fn).run("look", deps=deps))
    assert result.output == "done"
    returns = [
        p
        for p in _tool_return_part(result)
        if isinstance(p, ToolReturnPart) and p.tool_name == "view_resource"
    ]
    assert len(returns) == 1
    content = returns[0].content
    assert isinstance(content, BinaryContent)
    assert content.data == b"\xff\xd8jpeg"
    assert content.media_type == "image/jpeg"


def test_view_resource_retries_on_unknown_body_path():
    def fn(messages, info):
        if not any(
            isinstance(p, RetryPromptPart)
            for m in messages
            for p in getattr(m, "parts", [])
        ):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="view_resource", args={"body_path": "made/up"}
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("done")])

    deps = _deps({"bodies/a.jpg": "image/jpeg"})
    result = asyncio.run(_view_agent(fn).run("look", deps=deps))
    retries = [
        p
        for p in _tool_return_part(result)
        if isinstance(p, RetryPromptPart) and p.tool_name == "view_resource"
    ]
    assert len(retries) == 1
    assert "no captured resource" in retries[0].content


def test_view_resource_retries_on_unsupported_media_type():
    def fn(messages, info):
        if not any(
            isinstance(p, RetryPromptPart)
            for m in messages
            for p in getattr(m, "parts", [])
        ):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="view_resource", args={"body_path": "bodies/v.svg"}
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("done")])

    deps = _deps({"bodies/v.svg": "image/svg+xml"})
    result = asyncio.run(_view_agent(fn).run("look", deps=deps))
    retries = [
        p
        for p in _tool_return_part(result)
        if isinstance(p, RetryPromptPart) and p.tool_name == "view_resource"
    ]
    assert len(retries) == 1
    assert "image/svg+xml" in retries[0].content


def test_view_resource_reads_through_dependencies():
    """The tool must fetch bytes via deps.read_resource, not by itself."""
    seen: list[str] = []

    def fn(messages, info):
        if not any(
            isinstance(p, ToolReturnPart)
            for m in messages
            for p in getattr(m, "parts", [])
        ):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="view_resource", args={"body_path": "bodies/p.png"}
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("done")])

    async def read_resource(body_path: str) -> bytes:
        seen.append(body_path)
        return b"pngdata"

    deps = ExtractionDependencies(
        brief=Brief(
            people_sought="Board members",
            subject_label="Organization",
            subject="Example Foundation",
        ),
        read_resource=read_resource,
        resource_media_types={"bodies/p.png": "image/png"},
    )
    result = asyncio.run(_view_agent(fn).run("look", deps=deps))
    assert seen == ["bodies/p.png"]
    returns = [
        p
        for p in _tool_return_part(result)
        if isinstance(p, ToolReturnPart) and p.tool_name == "view_resource"
    ]
    assert returns[0].content == BinaryContent(b"pngdata", media_type="image/png")


# --- full runs of the real extraction agent, its output union, and the
# view_resource tool wired exactly as the worker wires them ---


def test_agent_function_model_returns_hit_with_nested_graph():
    def fn(messages, info):
        return ModelResponse(
            parts=[TextPart(_native_text("Hit", {"kind": "hit", "persons": [_PERSON]}))]
        )

    with extraction_agent.override(model=FunctionModel(fn)):
        result = asyncio.run(extraction_agent.run("ignored prompt", deps=_deps({})))

    assert isinstance(result.output, Hit)
    [returned] = result.output.persons
    assert isinstance(returned, Person)
    assert returned.name == "Amina Diallo"
    assert returned.countries == ["Senegal"]
    assert [p.name for p in returned.positions] == ["Director", "Chair"]
    assert isinstance(returned.positions[0], Position)
    assert returned.positions[0].organization == "Example Foundation"
    assert returned.positions[0].start_date == "2021"


def test_agent_function_model_returns_miss():
    def fn(messages, info):
        return ModelResponse(
            parts=[
                TextPart(
                    _native_text(
                        "Miss",
                        {"kind": "miss", "reason": "No officeholders; news article."},
                    )
                )
            ]
        )

    with extraction_agent.override(model=FunctionModel(fn)):
        result = asyncio.run(extraction_agent.run("ignored prompt", deps=_deps({})))

    assert isinstance(result.output, Miss)
    assert result.output.reason == "No officeholders; news article."


def test_agent_function_model_returns_broken_snapshot():
    def fn(messages, info):
        return ModelResponse(
            parts=[
                TextPart(
                    _native_text(
                        "BrokenSnapshot",
                        {"kind": "broken", "reason": "Cloudflare challenge"},
                    )
                )
            ]
        )

    with extraction_agent.override(model=FunctionModel(fn)):
        result = asyncio.run(extraction_agent.run("ignored prompt", deps=_deps({})))

    assert isinstance(result.output, BrokenSnapshot)
    assert result.output.reason == "Cloudflare challenge"


def test_agent_test_model_views_resource_and_produces_valid_result():
    # TestModel generates 'a' for a string argument, so the media-type map
    # must carry that body path for the generated tool call to succeed. Its
    # default profile claims no native JSON-schema support and its generated
    # text would not satisfy the schema, so hand it a valid union payload
    # (miss: this stub does not model real page content) as the final text.
    deps = _deps({"a": "image/png"}, b"\x89PNG-fake")
    test_model = TestModel(
        profile={"supports_json_schema_output": True},
        custom_output_text=_native_text(
            "Miss", {"kind": "miss", "reason": "No officeholders on the page."}
        ),
    )
    with extraction_agent.override(model=test_model):
        result = asyncio.run(extraction_agent.run("ignored prompt", deps=deps))

    assert isinstance(result.output, Miss)
    assert result.output.reason == "No officeholders on the page."
    parts = _tool_return_part(result)
    [call] = [
        p
        for p in parts
        if isinstance(p, ToolCallPart) and p.tool_name == "view_resource"
    ]
    assert call.args == {"body_path": "a"}
    [image_return] = [
        p
        for p in parts
        if isinstance(p, ToolReturnPart) and p.tool_name == "view_resource"
    ]
    assert image_return.content == BinaryContent(
        b"\x89PNG-fake", media_type="image/png"
    )


# --- prompt construction ---


def test_build_prompt_contains_only_delimited_page_context():
    metadata = PageMetadata(
        requested_url="https://example.org/x",
        final_url="https://example.org/y",
        title="T",
        description=None,
    )
    prompt = build_prompt(metadata, '- h1 "Cabinet"')
    assert prompt.startswith("<page_snapshot>")
    assert "<objective>" not in prompt
    assert "<page_metadata>" in prompt
    assert "<page_outline>" in prompt


def test_build_prompt_marks_context_and_includes_error():
    metadata = PageMetadata(
        requested_url="https://example.org/x",
        final_url="https://example.org/y",
        http_status=503,
        capture_error="navigation timeout",
        title="T",
        description=None,
    )
    outline = '- main:\n  - h1 "Board"\n  - img "logo" [src=https://example.org/l.png] [body=bodies/l.png]'
    prompt = build_prompt(metadata, outline)
    assert "<page_snapshot>" in prompt and "</page_snapshot>" in prompt
    assert "Requested URL: https://example.org/x" in prompt
    assert "Final URL: https://example.org/y" in prompt
    assert "HTTP status: 503" in prompt
    assert "Capture error: navigation timeout" in prompt
    assert "Document title: T" in prompt
    assert "Meta description: [not provided]" in prompt
    assert f"<page_outline>\n{outline}\n</page_outline>" in prompt


def test_build_prompt_omits_absent_optional_fields():
    metadata = PageMetadata(
        requested_url="https://example.org",
        final_url="https://example.org",
        title=None,
        description=None,
    )
    prompt = build_prompt(metadata, '- text: "empty"')
    assert "Capture error" not in prompt
    assert "HTTP status: [not provided]" in prompt
    assert '<page_outline>\n- text: "empty"\n</page_outline>' in prompt


# --- metadata_from_html ---


def test_metadata_from_html_fields():
    html = """
    <html><head><title>Board — Example</title>
    <meta name="description" content="The board.">
    </head><body></body></html>
    """
    metadata = metadata_from_html(
        "https://example.org/board",
        html,
        final_url="https://www.example.org/board/",
        http_status=200,
        capture_error=None,
    )
    assert metadata.requested_url == "https://example.org/board"
    assert metadata.final_url == "https://www.example.org/board/"
    assert metadata.http_status == 200
    assert metadata.capture_error is None
    assert metadata.title == "Board — Example"
    assert metadata.description == "The board."


def test_metadata_from_html_requires_final_url():
    with pytest.raises(TypeError):
        metadata_from_html("https://example.org", "<html></html>")


def test_metadata_from_html_empty_page():
    metadata = metadata_from_html(
        "https://example.org", "<html></html>", final_url="https://example.org"
    )
    assert metadata.title is None
    assert metadata.description is None
    assert metadata.http_status is None
    assert metadata.capture_error is None


# --- discovery schema ---


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
        CandidateLink(url="https://example.org/board", text="Board of Directors"),
        CandidateLink(url="https://example.org/team?ref=nav", text="Our Team"),
        CandidateLink(url="https://example.org/müller", text="Vorstand (Müller)"),
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
    # No page context beyond the links themselves.
    assert "<page_outline>" not in prompt
    assert "<page_snapshot>" not in prompt


def test_build_discovery_prompt_without_links():
    assert build_discovery_prompt([]) == "<candidate_links>\n</candidate_links>"


# --- shared brief instructions ---


def test_both_agents_receive_the_same_brief_instructions():
    brief = "People sought: Board members\nOrganization: Example Foundation"
    assert render_brief(_BRIEF) == brief
    seen: dict[str, str] = {}

    def discovery_fn(messages, info):
        seen["discovery"] = info.instructions
        return ModelResponse(parts=[TextPart(json.dumps({"selections": []}))])

    def extraction_fn(messages, info):
        seen["extraction"] = info.instructions
        return ModelResponse(
            parts=[
                TextPart(
                    _native_text(
                        "Miss", {"kind": "miss", "reason": "No board members here."}
                    )
                )
            ]
        )

    with discovery_agent.override(model=FunctionModel(discovery_fn)):
        asyncio.run(discovery_agent.run("links", deps=_BRIEF))
    with extraction_agent.override(model=FunctionModel(extraction_fn)):
        asyncio.run(extraction_agent.run("ignored prompt", deps=_deps({})))

    assert brief in seen["discovery"]
    assert brief in seen["extraction"]
