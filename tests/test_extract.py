"""Tests for the extraction schema, prompts, tools, and result union."""

import asyncio

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
    BrokenSnapshot,
    ExtractionDependencies,
    Hit,
    Miss,
    PageMetadata,
    PageResult,
    Person,
    Position,
    build_prompt,
    extraction_agent,
    metadata_from_html,
    view_resource,
)

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


# --- union discrimination and validation ---


def test_hit_requires_at_least_one_person():
    assert (
        Hit(persons=[{"name": "A", "positions": [{"name": "Director"}]}]).kind == "hit"
    )
    with pytest.raises(ValidationError):
        Hit(persons=[])
    with pytest.raises(ValidationError):
        Hit()


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
        result_adapter.validate_python({"kind": "broken", "reason": "404"}),
        BrokenSnapshot,
    )
    assert (
        result_adapter.validate_python({"kind": "broken", "reason": " 404 "}).reason
        == "404"
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
        read_resource=read_resource, resource_media_types=media_types
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
        read_resource=read_resource, resource_media_types={"bodies/p.png": "image/png"}
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
            parts=[
                ToolCallPart("final_result_Hit", {"kind": "hit", "persons": [_PERSON]})
            ]
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
                ToolCallPart(
                    "final_result_Miss",
                    {"kind": "miss", "reason": "No officeholders; news article."},
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
                ToolCallPart(
                    "final_result_BrokenSnapshot",
                    {"kind": "broken", "reason": "Cloudflare challenge"},
                )
            ]
        )

    with extraction_agent.override(model=FunctionModel(fn)):
        result = asyncio.run(extraction_agent.run("ignored prompt", deps=_deps({})))

    assert isinstance(result.output, BrokenSnapshot)
    assert result.output.reason == "Cloudflare challenge"


def test_agent_test_model_views_resource_and_produces_valid_result():
    # TestModel generates 'a' for a string argument, so the media-type map
    # must carry that body path for the generated tool call to succeed.
    deps = _deps({"a": "image/png"}, b"\x89PNG-fake")
    with extraction_agent.override(model=TestModel()):
        result = asyncio.run(extraction_agent.run("ignored prompt", deps=deps))

    # TestModel satisfies the schema with a minimal valid output; the run
    # must end in a member of the union, whichever member it picks.
    assert isinstance(result.output, (Hit, Miss, BrokenSnapshot))
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


def test_build_prompt_includes_delimited_objective_before_page_context():
    metadata = PageMetadata(
        requested_url="https://example.org/x",
        final_url="https://example.org/y",
        title="T",
        description=None,
    )
    objective = "Identify the current members of the country's cabinet."
    prompt = build_prompt(objective, metadata, '- h1 "Cabinet"')
    assert "<objective>" in prompt and "</objective>" in prompt
    assert objective in prompt
    # The objective block comes before the page source context.
    assert prompt.index("<objective>") < prompt.index("<page_metadata>")
    assert prompt.index("<objective>") < prompt.index("<page_outline>")


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
    prompt = build_prompt("Find the board members.", metadata, outline)
    assert "Requested URL (context only): https://example.org/x" in prompt
    assert "Final URL (context only): https://example.org/y" in prompt
    assert "HTTP status (context only): 503" in prompt
    assert "Capture error (context only): navigation timeout" in prompt
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
    prompt = build_prompt("List the mayors.", metadata, '- text: "empty"')
    assert "Capture error" not in prompt
    assert "HTTP status (context only): [not provided]" in prompt
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
