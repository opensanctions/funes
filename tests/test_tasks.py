"""Tests for the Procrastinate app's task registration, routing, and payload."""

import asyncio
import uuid

import pytest

from funes.procrastinate import app
from funes.tasks import repair_snapshot


def test_task_registration_names_and_queues() -> None:
    app.perform_import_paths()
    assert {"funes.inspect_candidate", "funes.repair_snapshot"} <= set(app.tasks)
    assert app.tasks["funes.inspect_candidate"].queue == "process"
    assert app.tasks["funes.repair_snapshot"].queue == "repair"


def test_repair_snapshot_not_implemented() -> None:
    attempt_id = str(uuid.uuid4())
    with pytest.raises(NotImplementedError, match=attempt_id):
        asyncio.run(repair_snapshot(attempt_id=attempt_id))
