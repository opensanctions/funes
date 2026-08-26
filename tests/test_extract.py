"""Tests for the extraction schema, prompts, and broken-page union."""

from pydantic import TypeAdapter, ValidationError

from funes.extract import (
    BrokenPage,
    Extraction,
    PageMetadata,
    PageResult,
    Person,
    Position,
    build_extraction_agent,
    metadata_from_html,
    prompt_content,
)

result_adapter = TypeAdapter(PageResult)


def test_extraction_default_kind():
    extraction = Extraction()
    assert extraction.kind == "extraction"
    assert extraction.persons == []


def test_broken_page_default_kind_and_reason_required():
    broken = BrokenPage(reason="Cloudflare challenge")
    assert broken.kind == "broken"
    try:
        BrokenPage()
    except ValidationError:
        pass
    else:
        raise AssertionError("reason must be required")
    try:
        BrokenPage(reason="")
    except ValidationError:
        pass
    else:
        raise AssertionError("reason must be non-empty")


def test_union_discriminates_on_kind():
    assert isinstance(
        result_adapter.validate_python({"kind": "extraction"}), Extraction
    )
    assert isinstance(
        result_adapter.validate_python({"kind": "broken", "reason": "404"}), BrokenPage
    )
    try:
        result_adapter.validate_python({"kind": "other"})
    except ValidationError:
        pass
    else:
        raise AssertionError("unknown kind must be rejected")


def test_agent_returns_union_output_type(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    agent = build_extraction_agent("gpt-5")
    assert agent.output_type is not None
    # The agent's structured output must be the union of both result kinds.
    assert agent.output_type == Extraction | BrokenPage


def test_extraction_with_persons_still_valid():
    extraction = result_adapter.validate_python(
        {
            "kind": "extraction",
            "persons": [{"name": "Amina Diallo", "positions": [{"name": "Director"}]}],
        }
    )
    assert isinstance(extraction, Extraction)
    person = extraction.persons[0]
    assert isinstance(person, Person)
    assert isinstance(person.positions[0], Position)


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


def test_metadata_from_html_defaults_final_url_to_requested():
    metadata = metadata_from_html("https://example.org", "<html></html>")
    assert metadata.final_url == "https://example.org"
    assert metadata.title is None
    assert metadata.description is None
    assert metadata.http_status is None


def test_prompt_content_marks_context_and_includes_error():
    metadata = PageMetadata(
        requested_url="https://example.org/x",
        final_url="https://example.org/y",
        http_status=503,
        capture_error="navigation timeout",
        title="T",
        description=None,
    )
    prompt = prompt_content(metadata, "body text")
    assert "Requested URL (context only): https://example.org/x" in prompt
    assert "Final URL (context only): https://example.org/y" in prompt
    assert "HTTP status (context only): 503" in prompt
    assert "Capture error (context only): navigation timeout" in prompt
    assert "Document title: T" in prompt
    assert "Meta description: [not provided]" in prompt
    assert "<page_text>\nbody text\n</page_text>" in prompt


def test_prompt_content_omits_absent_optional_fields():
    metadata = PageMetadata(
        requested_url="https://example.org",
        final_url="https://example.org",
        title=None,
        description=None,
    )
    prompt = prompt_content(metadata, "text")
    assert "Capture error" not in prompt
    assert "HTTP status (context only): [not provided]" in prompt
