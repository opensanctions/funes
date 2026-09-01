"""Read input YAML dataset files into a dataset/subject/url catalogue."""

import os
from typing import Annotated
from urllib.parse import urldefrag

import fsspec
import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

# Catalogue strings are non-blank, with surrounding whitespace stripped.
_Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SubjectDefinition(BaseModel):
    """One named subject within a dataset and the URLs known for it.

    ``urls`` may be empty while a future spider discovers pages for the
    subject.
    """

    model_config = ConfigDict(extra="forbid")

    name: _Label
    urls: list[_Label] = Field(default_factory=list)

    @field_validator("urls")
    @classmethod
    def strip_url_fragments(cls, urls: list[str]) -> list[str]:
        """Catalogue URLs are fragment-free: fragments never reach the server."""
        stripped = [urldefrag(url)[0] for url in urls]
        if any(not url for url in stripped):
            raise ValueError("catalogue urls must not be bare fragments")
        return stripped


class DatasetDefinition(BaseModel):
    """One input dataset: its filename stem plus the file's configuration.

    ``people_sought`` names the class of position holders the dataset is
    after; ``subject_label`` names the subject's role in the inspection
    brief (e.g. Organization, Court, Sending country).
    """

    model_config = ConfigDict(extra="forbid")

    name: _Label
    people_sought: _Label
    subject_label: _Label
    subjects: list[SubjectDefinition]

    @field_validator("subjects")
    @classmethod
    def reject_duplicate_subjects(
        cls, subjects: list[SubjectDefinition]
    ) -> list[SubjectDefinition]:
        seen: set[str] = set()
        for subject in subjects:
            if subject.name in seen:
                raise ValueError(f"duplicate subject: {subject.name!r}")
            seen.add(subject.name)
        return subjects


def load_datasets(base_path: str) -> list[DatasetDefinition]:
    """Load dataset definitions from every YAML file under an fsspec path.

    ``name`` is the YAML filename without extension (e.g.
    ``hio_leadership``); the file supplies ``people_sought``,
    ``subject_label``, and the ``subjects`` hierarchy. Files are read in
    sorted filename order.
    """
    fs, base = fsspec.core.url_to_fs(base_path)
    datasets: list[DatasetDefinition] = []
    for path in sorted(fs.glob(os.path.join(base, "*.yaml"))):
        name = os.path.splitext(os.path.basename(path))[0]
        with fs.open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        if not isinstance(raw, dict):
            raise TypeError(f"dataset file {path!r} must contain a YAML mapping")
        datasets.append(DatasetDefinition(name=name, **raw))
    return datasets
