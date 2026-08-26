"""Shared pytest configuration: no test may hit a real model API."""

import os

from pydantic_ai import models

# Provider construction requires a key in the environment; the value is a
# placeholder because ALLOW_MODEL_REQUESTS blocks any real request with it.
os.environ.setdefault("OPENAI_API_KEY", "test")
models.ALLOW_MODEL_REQUESTS = False
