"""Case input types for the inspection eval suite."""

from pydantic import BaseModel, Field


class FixtureInput(BaseModel):
    """One frozen page: the fixture file stem, its fictional final URL, and
    the objective the inspection runs against."""

    fixture: str
    url: str
    objective: str = Field(
        min_length=1, description="What the requester wants to learn from the page."
    )
