"""Tests for the Procrastinate app's task registration, routing, and payload."""

import asyncio
import uuid

import pytest

from funes.procrastinate import app
from funes.tasks import Queue, Task, repair_snapshot


def test_registers_both_tasks() -> None:
    app.perform_import_paths()
    assert {Task.INSPECT_CANDIDATE, Task.REPAIR_SNAPSHOT} <= set(app.tasks)


def test_task_queues() -> None:
    app.perform_import_paths()
    assert app.tasks[Task.INSPECT_CANDIDATE].queue == Queue.PROCESS
    assert app.tasks[Task.REPAIR_SNAPSHOT].queue == Queue.REPAIR


def test_exact_registered_names() -> None:
    app.perform_import_paths()
    assert {"funes.inspect_candidate", "funes.repair_snapshot"} <= set(app.tasks)
    assert Queue.PROCESS == "process"
    assert Queue.REPAIR == "repair"


def test_repair_snapshot_payload_is_only_attempt_id() -> None:
    """Repair routes by completed attempt id alone; any other payload is rejected."""
    func = app.tasks[Task.REPAIR_SNAPSHOT].func
    with pytest.raises(TypeError):
        asyncio.run(func())
    with pytest.raises(TypeError):
        asyncio.run(func(page_id=str(uuid.uuid4()), snapshot_id=str(uuid.uuid4())))


def test_repair_snapshot_not_implemented() -> None:
    attempt_id = str(uuid.uuid4())
    with pytest.raises(NotImplementedError, match=attempt_id):
        asyncio.run(repair_snapshot(attempt_id=attempt_id))
