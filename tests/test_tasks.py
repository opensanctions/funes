"""Tests for the Procrastinate app's task registration and routing."""

from funes.config import (
    Config,
    InputConfig,
    ModelConfig,
    PravdaSettings,
    SessionsConfig,
)
from funes.tasks import (
    Queue,
    Task,
    build_app,
)


def make_config() -> Config:
    return Config(
        pravda=PravdaSettings(
            database_url="postgresql+asyncpg://funes:funes@localhost/funes",
            browser_ws_url="ws://localhost:3000",
            storage_base_path="file:///tmp/funes",
        ),
        model=ModelConfig(name="test-model"),
        input=InputConfig(base_path="file:///tmp/input"),
        sessions=SessionsConfig(base_path="/tmp/sessions"),
    )


def test_registers_both_tasks() -> None:
    app = build_app(make_config())
    assert {Task.PROCESS_PAGE, Task.REVIEW_BROKEN_PAGE} <= set(app.tasks)


def test_task_queues() -> None:
    app = build_app(make_config())
    assert app.tasks[Task.PROCESS_PAGE].queue == Queue.PROCESS
    assert app.tasks[Task.REVIEW_BROKEN_PAGE].queue == Queue.REVIEW
