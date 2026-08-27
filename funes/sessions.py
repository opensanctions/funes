"""Persist extraction agent message histories as JSON files."""

import os


def session_path(base_path: str, run_id: str) -> str:
    """Return the JSON path for one agent session inside ``base_path``."""
    return os.path.join(base_path, f"{run_id}.json")


def write_session(path: str, messages_json: bytes) -> None:
    """Write a session file: one agent run's message history as JSON bytes.

    The bytes come from ``AgentRunResult.all_messages_json()``; reload a
    session with ``ModelMessagesTypeAdapter.validate_json``.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(messages_json)
