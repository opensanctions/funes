"""Persist extraction agent message histories as JSON files."""

import os

from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelMessage


def session_path(base_path: str, run_id: str) -> str:
    """Return the JSON path for one agent session inside ``base_path``."""
    return os.path.join(base_path, f"{run_id}.json")


def write_session(path: str, messages: list[ModelMessage]) -> None:
    """Write a session file: the whole message history as one JSON array."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(ModelMessagesTypeAdapter.dump_json(messages))
