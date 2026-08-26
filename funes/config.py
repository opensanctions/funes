"""Typed application configuration loaded from the environment."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class PravdaSettings:
    """Connection settings for Pravda and its shared infrastructure."""

    database_url: str
    browser_ws_url: str
    storage_base_path: str


@dataclass(frozen=True)
class ModelConfig:
    name: str


@dataclass(frozen=True)
class InputConfig:
    base_path: str


@dataclass(frozen=True)
class SessionsConfig:
    base_path: str


@dataclass(frozen=True)
class Config:
    pravda: PravdaSettings
    model: ModelConfig
    input: InputConfig
    sessions: SessionsConfig


def load_config() -> Config:
    """Load and validate all settings from the environment (and ``.env``)."""
    load_dotenv()
    return Config(
        pravda=PravdaSettings(
            database_url=os.environ["PRAVDA_DATABASE_URL"],
            browser_ws_url=os.environ["PRAVDA_BROWSER_WS_URL"],
            storage_base_path=os.environ["PRAVDA_STORAGE_BASE_PATH"],
        ),
        model=ModelConfig(name=os.environ["OPENAI_MODEL"]),
        input=InputConfig(
            base_path=os.environ["INPUT_BASE_PATH"],
        ),
        sessions=SessionsConfig(
            base_path=os.environ["SESSIONS_BASE_PATH"],
        ),
    )
