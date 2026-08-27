"""Case input types for the inspection eval suite."""

from pydantic import BaseModel


class FixtureInput(BaseModel):
    """One frozen capture and the objective inspected against it."""

    fixture: str
    url: str
    objective: str
    final_url: str | None = None
    http_status: int | None = 200
    capture_error: str | None = None
