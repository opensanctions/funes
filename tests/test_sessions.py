"""Tests for agent session persistence."""

from pathlib import Path

from pydantic_ai import BinaryContent, ModelMessagesTypeAdapter
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)

from funes.sessions import write_session


def test_write_session_creates_directories_and_round_trips(tmp_path):
    path = str(tmp_path / "nested" / "dir" / "run-1.json")
    messages = [
        ModelRequest(parts=[UserPromptPart(content="extract this page")]),
        ModelResponse(parts=[TextPart("done")], model_name="test"),
    ]
    write_session(path, ModelMessagesTypeAdapter.dump_json(messages))

    restored = ModelMessagesTypeAdapter.validate_json(Path(path).read_bytes())
    assert restored == messages


def test_write_session_round_trips_binary_tool_returns(tmp_path):
    # Sessions written after a view_resource call carry image bytes; they
    # must survive the JSON round trip production uses for replay.
    path = str(tmp_path / "run-2.json")
    binary = BinaryContent(b"\x89PNG-fake", media_type="image/png")
    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="view_resource",
                    content=binary,
                    tool_call_id="call-1",
                )
            ]
        ),
    ]
    write_session(path, ModelMessagesTypeAdapter.dump_json(messages))

    restored = ModelMessagesTypeAdapter.validate_json(Path(path).read_bytes())
    # Image content is restored as its BinaryImage specialization.
    content = restored[0].parts[0].content
    assert isinstance(content, BinaryContent)
    assert content.data == b"\x89PNG-fake"
    assert content.media_type == "image/png"
