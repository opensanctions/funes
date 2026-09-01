"""Behavior tests for input YAML catalogue loading."""

import fsspec
import pytest
from pydantic import ValidationError

from funes.sources import DatasetDefinition, SubjectDefinition, load_datasets


def write_yaml(fs, base, name, content):
    fs.mkdirs(base, exist_ok=True)
    with fs.open(f"{base}/{name}", "w", encoding="utf-8") as f:
        f.write(content)


def test_load_datasets_builds_hierarchy_in_filename_order(tmp_path):
    fs = fsspec.filesystem("memory")
    base = f"memory://{tmp_path.name}/inputs"
    write_yaml(
        fs,
        base,
        "two.yaml",
        """\
people_sought: Judges
subject_label: Court
subjects:
  - name: Court C
    urls: []
""",
    )
    write_yaml(
        fs,
        base,
        "one.yaml",
        """\
people_sought: Heads
subject_label: Organization
subjects:
  - name: Org A
    urls:
      - https://a.example
      - https://a.example/about
  - name: Org B
""",
    )

    assert load_datasets(base) == [
        DatasetDefinition(
            name="one",
            people_sought="Heads",
            subject_label="Organization",
            subjects=[
                SubjectDefinition(
                    name="Org A",
                    urls=["https://a.example", "https://a.example/about"],
                ),
                SubjectDefinition(name="Org B"),
            ],
        ),
        DatasetDefinition(
            name="two",
            people_sought="Judges",
            subject_label="Court",
            subjects=[SubjectDefinition(name="Court C")],
        ),
    ]


def test_load_datasets_strips_catalogue_strings(tmp_path):
    fs = fsspec.filesystem("memory")
    base = f"memory://{tmp_path.name}/inputs"
    write_yaml(
        fs,
        base,
        "one.yaml",
        """\
people_sought: "  Heads  "
subject_label: "  Organization  "
subjects:
  - name: "  Org A  "
    urls: ["  https://a.example  "]
""",
    )

    assert load_datasets(base) == [
        DatasetDefinition(
            name="one",
            people_sought="Heads",
            subject_label="Organization",
            subjects=[SubjectDefinition(name="Org A", urls=["https://a.example"])],
        )
    ]


def test_load_datasets_strips_url_fragments(tmp_path):
    fs = fsspec.filesystem("memory")
    base = f"memory://{tmp_path.name}/inputs"
    write_yaml(
        fs,
        base,
        "one.yaml",
        """\
people_sought: Heads
subject_label: Organization
subjects:
  - name: Org A
    urls:
      - https://a.example/board#members
      - https://a.example/about
""",
    )

    assert load_datasets(base) == [
        DatasetDefinition(
            name="one",
            people_sought="Heads",
            subject_label="Organization",
            subjects=[
                SubjectDefinition(
                    name="Org A",
                    urls=["https://a.example/board", "https://a.example/about"],
                )
            ],
        )
    ]


def test_load_datasets_rejects_bare_fragment_urls(tmp_path):
    fs = fsspec.filesystem("memory")
    base = f"memory://{tmp_path.name}/inputs"
    write_yaml(
        fs,
        base,
        "one.yaml",
        """\
people_sought: Heads
subject_label: Organization
subjects:
  - name: Org A
    urls: ["#members"]
""",
    )

    with pytest.raises(ValidationError, match="bare fragment"):
        load_datasets(base)


def test_load_datasets_rejects_duplicate_subjects(tmp_path):
    fs = fsspec.filesystem("memory")
    base = f"memory://{tmp_path.name}/inputs"
    write_yaml(
        fs,
        base,
        "one.yaml",
        """\
people_sought: Heads
subject_label: Organization
subjects:
  - name: Org A
  - name: Org A
""",
    )

    with pytest.raises(ValidationError, match="duplicate subject"):
        load_datasets(base)
