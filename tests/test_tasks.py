"""Tests for the Procrastinate app's task registration and routing."""

from funes.procrastinate import app
from funes.tasks import Queue, Task


def test_registers_both_tasks() -> None:
    app.perform_import_paths()
    assert {Task.PROCESS_PAGE, Task.REVIEW_BROKEN_PAGE} <= set(app.tasks)


def test_task_queues() -> None:
    app.perform_import_paths()
    assert app.tasks[Task.PROCESS_PAGE].queue == Queue.PROCESS
    assert app.tasks[Task.REVIEW_BROKEN_PAGE].queue == Queue.REVIEW
