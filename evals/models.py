"""Case input types for the inspection eval suite."""

from pydantic import BaseModel


class FixtureInput(BaseModel):
    """One frozen page: the fixture file stem, its fictional final URL, and
    the objective the inspection runs against."""

    fixture: str
    url: str
    objective: str
