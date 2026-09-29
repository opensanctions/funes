"""Typed application configuration loaded from the environment."""

import os
from dataclasses import dataclass
from datetime import timedelta

from pravda import PravdaConfig


@dataclass(frozen=True)
class Config:
    database_url: str
    pravda: PravdaConfig
    model: str
    input_base_path: str
    sessions_base_path: str
    revisit_interval: timedelta


def load_config() -> Config:
    """Load and validate all settings from the environment."""
    return Config(
        database_url=os.environ["FUNES_DATABASE_URI"],
        pravda=PravdaConfig(
            browser_ws_url=os.environ["PRAVDA_BROWSER_WS_URL"],
            storage_base_path=os.environ["PRAVDA_STORAGE_BASE_PATH"],
        ),
        model=os.environ["MODEL"],
        input_base_path=os.environ["INPUT_BASE_PATH"],
        sessions_base_path=os.environ["SESSIONS_BASE_PATH"],
        revisit_interval=timedelta(days=float(os.environ["REVISIT_INTERVAL_DAYS"])),
    )
