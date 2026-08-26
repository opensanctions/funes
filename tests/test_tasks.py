"""Tests for the Procrastinate app's task registration and routing."""

import asyncio

import pytest

from funes.config import (
    Config,
    InputConfig,
    ModelConfig,
    PravdaSettings,
    SessionsConfig,
)
from funes.tasks import (
    QUEUE_BROKEN,
    QUEUE_PIPELINE,
    TASK_PROCESS_PAGE,
    TASK_REVIEW_BROKEN_PAGE,
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
        input=InputConfig(input_base_path="file:///tmp/input"),
        sessions=SessionsConfig(base_path="/tmp/sessions"),
    )


def test_queue_names() -> None:
    assert QUEUE_PIPELINE == "pipeline"
    assert QUEUE_BROKEN == "broken"


def test_registers_both_tasks() -> None:
    app = build_app(make_config())
    assert {TASK_PROCESS_PAGE, TASK_REVIEW_BROKEN_PAGE} <= set(app.tasks)


def test_task_queues() -> None:
    app = build_app(make_config())
    assert app.tasks[TASK_PROCESS_PAGE].queue == QUEUE_PIPELINE
    assert app.tasks[TASK_REVIEW_BROKEN_PAGE].queue == QUEUE_BROKEN


def test_review_task_raises_when_invoked() -> None:
    app = build_app(make_config())
    review = app.tasks[TASK_REVIEW_BROKEN_PAGE]
    with pytest.raises(NotImplementedError):
        asyncio.run(
            review(
                page_id="00000000-0000-0000-0000-000000000001",
                snapshot_id="00000000-0000-0000-0000-000000000002",
                run_id="00000000-0000-0000-0000-000000000003",
                reason="missing artifact(s): plaintext",
            )
        )
