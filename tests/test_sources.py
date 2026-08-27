"""Unit tests for input CSV loading."""

import fsspec
import pytest


def write_csv(fs, base, name, content):
    fs.mkdirs(base, exist_ok=True)
    with fs.open(f"{base}/{name}", "w", encoding="utf-8") as f:
        f.write(content)


@pytest.fixture
def memory_fs():
    fs = fsspec.filesystem("memory")
    yield fs
    fs.store.clear()


def test_load_inputs_groups_rows_by_csv_in_filename_order(tmp_path, memory_fs):
    base = f"memory://{tmp_path.name}/inputs"
    write_csv(
        memory_fs,
        base,
        "two.csv",
        "objective,url\nHeads of Org C,https://c.example\n",
    )
    write_csv(
        memory_fs,
        base,
        "one.csv",
        "objective,url\nHeads of Org A,https://a.example\nHeads of Org B,https://b.example\n",
    )
    from funes.sources import load_inputs

    assert load_inputs(base) == [
        ("one", "Heads of Org A", "https://a.example"),
        ("one", "Heads of Org B", "https://b.example"),
        ("two", "Heads of Org C", "https://c.example"),
    ]


def test_load_inputs_strips_whitespace(tmp_path, memory_fs):
    base = f"memory://{tmp_path.name}/inputs"
    write_csv(
        memory_fs,
        base,
        "one.csv",
        "objective,url\n  Heads of Org A  ,  https://a.example  \n",
    )
    from funes.sources import load_inputs

    assert load_inputs(base) == [("one", "Heads of Org A", "https://a.example")]
