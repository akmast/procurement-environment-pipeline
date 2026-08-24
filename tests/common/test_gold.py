"""Unit tests for common/gold.py's build_gold_table()/write_gold_table() —
the shared select+order+rename+dedup+write logic every gold/<source>/*.py
module relies on."""
import logging
from io import BytesIO

import pandas as pd

from common.gold import build_gold_table, write_gold_table


def _write(tmp_path, monkeypatch, relative_path, df):
    monkeypatch.setattr("common.storage.PROJECT_ROOT", tmp_path)
    full_path = tmp_path / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    buffer = BytesIO()
    df.to_parquet(buffer, index=False)
    full_path.write_bytes(buffer.getvalue())


def test_combines_multiple_files_keeps_order_and_renames(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "a.parquet",
           pd.DataFrame([{"code": "DE", "value": 1.0, "extra": "x"}]))
    _write(tmp_path, monkeypatch, "b.parquet",
           pd.DataFrame([{"code": "PL", "value": 2.0, "extra": "y"}]))

    df = build_gold_table(["a.parquet", "b.parquet"], "local", ["code", "value"], rename={"code": "country"})

    assert list(df.columns) == ["country", "value"]
    assert sorted(df["country"]) == ["DE", "PL"]
    assert "extra" not in df.columns


def test_deduplicates_exact_repeat_rows(tmp_path, monkeypatch):
    row = {"code": "DE", "value": 1.0}
    _write(tmp_path, monkeypatch, "a.parquet", pd.DataFrame([row]))
    _write(tmp_path, monkeypatch, "b.parquet", pd.DataFrame([row]))  # exact duplicate

    df = build_gold_table(["a.parquet", "b.parquet"], "local", ["code", "value"])

    assert len(df) == 1


def test_missing_column_logs_and_fills_nan_instead_of_raising(tmp_path, monkeypatch, caplog):
    _write(tmp_path, monkeypatch, "a.parquet", pd.DataFrame([{"code": "DE", "value": 1.0}]))
    _write(tmp_path, monkeypatch, "b.parquet", pd.DataFrame([{"code": "PL"}]))  # no "value" column

    with caplog.at_level(logging.WARNING):
        df = build_gold_table(["a.parquet", "b.parquet"], "local", ["code", "value"])

    assert "missing expected column" in caplog.text
    by_code = df.set_index("code")
    assert by_code.loc["DE", "value"] == 1.0
    assert pd.isna(by_code.loc["PL", "value"])


def test_empty_paths_returns_empty_dataframe_with_renamed_columns():
    df = build_gold_table([], "local", ["code", "value"], rename={"code": "country"})
    assert list(df.columns) == ["country", "value"]
    assert len(df) == 0


def test_write_gold_table_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr("common.storage.PROJECT_ROOT", tmp_path)
    df = pd.DataFrame([{"country": "DE", "value": 1.0}])
    write_gold_table(df, "out/gold.parquet", "local")

    read_back = pd.read_parquet(tmp_path / "out/gold.parquet")
    assert read_back.to_dict("records") == df.to_dict("records")
