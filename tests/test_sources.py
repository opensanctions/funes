"""Unit tests for input CSV loading."""

import fsspec


def write_csv(fs, base, name, content):
    fs.mkdirs(base, exist_ok=True)
    with fs.open(f"{base}/{name}", "w", encoding="utf-8") as f:
        f.write(content)


def test_load_inputs_merges_csvs_and_skips_blank_urls(tmp_path):
    base = f"memory://{tmp_path.name}/inputs"
    fs = fsspec.filesystem("memory")
    write_csv(
        fs,
        base,
        "one.csv",
        "organization,url\nOrg A,https://a.example\nOrg B,https://b.example\n",
    )
    write_csv(
        fs,
        base,
        "two.csv",
        "organization,url\nOrg C,\nOrg C,https://c.example\n",
    )
    try:
        from funes.sources import load_inputs

        assert load_inputs(base) == [
            ("https://a.example", "Org A"),
            ("https://b.example", "Org B"),
            ("https://c.example", "Org C"),
        ]
    finally:
        fs.rm(base, recursive=True)
