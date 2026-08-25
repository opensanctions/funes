"""Write-only persistence of extraction agent sessions as JSONL files.

Each session is one file named ``{run_id}.jsonl`` (one successful extraction
↔ one agent session, ``run_id`` being the extraction id). The format is the
documented pydantic-ai persistence layout:

- line 1: a header object (plain JSON), deliberately carrying no ``kind`` key
  so it cannot collide with pydantic-ai's message discriminator;
- lines 2..n: one ``ModelMessage`` per line, serialized with
  ``TypeAdapter(ModelMessage).dump_json()`` — usage counts and the structured
  output are embedded in the message lines themselves.

Reading sessions back is done with pydantic-ai's own
``ModelMessagesTypeAdapter``; this module only writes.
"""

import json
import os

from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage

_MESSAGE_ADAPTER = TypeAdapter(ModelMessage)


def session_path(base_path: str, run_id: str) -> str:
    """Return the JSONL path for one agent session inside ``base_path``."""
    return os.path.join(base_path, f"{run_id}.jsonl")


def write_session(path: str, header: dict, messages: list[ModelMessage]) -> None:
    """Write a session file: header line, then one message per line."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(json.dumps(header) + "\n")
        f.writelines(
            _MESSAGE_ADAPTER.dump_json(message).decode("utf-8") + "\n"
            for message in messages
        )
