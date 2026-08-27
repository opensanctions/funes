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


def test_load_inputs_groups_rows_by_csv(tmp_path, memory_fs):
    base = f"memory://{tmp_path.name}/inputs"
    write_csv(
        memory_fs,
        base,
        "one.csv",
        "objective,url\nHeads of Org A,https://a.example\nHeads of Org B,https://b.example\n",
    )
    write_csv(
        memory_fs,
        base,
        "two.csv",
        "objective,url\nHeads of Org C,https://c.example\n",
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


def test_load_inputs_missing_columns(tmp_path, memory_fs):
    base = f"memory://{tmp_path.name}/inputs"
    write_csv(memory_fs, base, "one.csv", "organization,url\nOrg A,https://a.example\n")
    from funes.sources import load_inputs

    with pytest.raises(ValueError, match="one.csv.*expected columns exactly"):
        load_inputs(base)


def test_load_inputs_extra_columns_rejected(tmp_path, memory_fs):
    base = f"memory://{tmp_path.name}/inputs"
    write_csv(
        memory_fs,
        base,
        "one.csv",
        "objective,url,organization\nHeads of Org A,https://a.example,Org A\n",
    )
    from funes.sources import load_inputs

    with pytest.raises(ValueError, match="one.csv.*expected columns exactly"):
        load_inputs(base)


def test_load_inputs_short_row(tmp_path, memory_fs):
    base = f"memory://{tmp_path.name}/inputs"
    write_csv(memory_fs, base, "one.csv", "objective,url\nHeads of Org A\n")
    from funes.sources import load_inputs

    with pytest.raises(ValueError, match=r"one.csv:2.*values for"):
        load_inputs(base)


def test_load_inputs_row_with_extra_values(tmp_path, memory_fs):
    base = f"memory://{tmp_path.name}/inputs"
    write_csv(
        memory_fs,
        base,
        "one.csv",
        "objective,url\nHeads of Org A,https://a.example,extra\n",
    )
    from funes.sources import load_inputs

    with pytest.raises(ValueError, match=r"one.csv:2.*more values than columns"):
        load_inputs(base)


def test_load_inputs_blank_objective(tmp_path, memory_fs):
    base = f"memory://{tmp_path.name}/inputs"
    write_csv(memory_fs, base, "one.csv", "objective,url\n,https://a.example\n")
    from funes.sources import load_inputs

    with pytest.raises(ValueError, match=r"one.csv:2.*objective"):
        load_inputs(base)


def test_load_inputs_blank_url(tmp_path, memory_fs):
    base = f"memory://{tmp_path.name}/inputs"
    write_csv(memory_fs, base, "one.csv", "objective,url\nHeads of Org A,\n")
    from funes.sources import load_inputs

    with pytest.raises(ValueError, match=r"one.csv:2.*url"):
        load_inputs(base)
