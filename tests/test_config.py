"""Tests for the typed configuration loader."""

from datetime import timedelta

import pytest

from funes.config import load_config

REQUIRED_VARS = {
    "PRAVDA_DATABASE_URL": "postgresql+psycopg://test:test@localhost:5432/test",
    "PRAVDA_BROWSER_WS_URL": "ws://localhost:3000",
    "PRAVDA_STORAGE_BASE_PATH": "memory://storage",
    "INPUT_BASE_PATH": "./datasets",
    "SESSIONS_BASE_PATH": "./sessions",
    "MODEL": "openai:test-model",
    "REVISIT_INTERVAL_DAYS": "30",
}


def set_env(monkeypatch: pytest.MonkeyPatch, interval: str) -> None:
    for key, value in REQUIRED_VARS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("REVISIT_INTERVAL_DAYS", interval)


def test_revisit_interval_whole_days(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, "30")
    assert load_config().revisit_interval == timedelta(days=30)


def test_revisit_interval_fractional_days(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, "0.5")
    assert load_config().revisit_interval == timedelta(days=0.5)


def test_revisit_interval_required(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, "30")
    monkeypatch.delenv("REVISIT_INTERVAL_DAYS")
    with pytest.raises(KeyError):
        load_config()
