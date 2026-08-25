"""Typed configuration, loaded once at the CLI boundary.

Settings are read here from the environment, never scattered as
``os.environ`` lookups through the business logic. Grouped frozen dataclasses
keep each subsystem's needs explicit, so a caller is handed only the slice it
uses (e.g. extraction gets ``ModelConfig`` / ``ImageConfig``, not the whole
config). ``load_config()`` is the single entry point and owns
``load_dotenv()``; importing this module has no side effects.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class PravdaSettings:
    """Environment-backed settings for Pravda's in-process async client.

    These three values are handed to Pravda's own ``PravdaConfig`` at the
    application boundary. The database URL is also used by Funes.
    """

    database_url: str
    browser_ws_url: str
    storage_base_path: str


@dataclass(frozen=True)
class ModelConfig:
    name: str


@dataclass(frozen=True)
class ImageConfig:
    tile_size: int
    tile_overlap: float


@dataclass(frozen=True)
class InputConfig:
    input_base_path: str


@dataclass(frozen=True)
class SessionsConfig:
    base_path: str


@dataclass(frozen=True)
class Config:
    pravda: PravdaSettings
    model: ModelConfig
    image: ImageConfig
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
        image=ImageConfig(
            tile_size=int(os.environ["IMAGE_TILE_SIZE"]),
            tile_overlap=float(os.environ["IMAGE_TILE_OVERLAP"]),
        ),
        input=InputConfig(
            input_base_path=os.environ["INPUT_BASE_PATH"],
        ),
        sessions=SessionsConfig(
            base_path=os.environ["SESSIONS_BASE_PATH"],
        ),
    )
