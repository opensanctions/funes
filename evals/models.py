"""Case input types for the extraction eval suite."""

from pydantic import BaseModel


class FixtureInput(BaseModel):
    """One frozen page: the fixture file stem and its fictional final URL."""

    fixture: str
    url: str
