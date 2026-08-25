"""Unit tests for common/gold.py's build_gold_table()/enforce_dtypes()/
drop_missing_required()/write_gold_table() — the shared select+order+
rename+dedup+cast+null-guard+write logic every gold/<source>/*.py
module relies on."""
import logging
from io import BytesIO

import pandas as pd
import pytest

from common.gold import build_gold_table, drop_missing_required, enforce_dtypes, write_gold_table


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


def test_enforce_dtypes_casts_every_kind():
    df = pd.DataFrame([
        {
            "code": 5,  # numeric-looking value in a column declared "string"
            "amount": "12.5",
            "count": "3",
            "ts": "2021-01-01T02:00:00",
            "day": "2021-01-01T00:00:00",  # "date" kind must drop the time component
        },
    ])

    result = enforce_dtypes(df, {
        "code": "string",
        "amount": "float64",
        "count": "Int64",
        "ts": "datetime64[ns]",
        "day": "date",
    })

    assert result["code"].dtype == "string"
    assert result["code"].iloc[0] == "5"  # never coerced to a number
    assert result["amount"].dtype == "float64"
    assert result["amount"].iloc[0] == 12.5
    assert str(result["count"].dtype) == "Int64"
    assert result["count"].iloc[0] == 3
    assert result["ts"].dtype == "datetime64[ns]"
    assert result["day"].iloc[0] == pd.Timestamp("2021-01-01").date()
    assert isinstance(result["day"].iloc[0], type(pd.Timestamp("2021-01-01").date()))


def test_enforce_dtypes_produces_consistent_type_across_mixed_input():
    # Simulates two partition files that drifted in dtype before this
    # column was ever cast — e.g. one file wrote pollutant_code as a
    # plain Python int, another as a string. Concatenating them without
    # enforce_dtypes() would leave pandas to infer a single dtype for
    # the mix (typically "object"), which is exactly what silently
    # produced a real-world Glue/Parquet type mismatch.
    df = pd.DataFrame({"pollutant_code": [5, "8"]})  # already object-dtype pre-cast
    result = enforce_dtypes(df, {"pollutant_code": "string"})
    assert result["pollutant_code"].dtype == "string"
    assert list(result["pollutant_code"]) == ["5", "8"]


def test_enforce_dtypes_ignores_absent_columns():
    df = pd.DataFrame([{"code": "DE"}])
    result = enforce_dtypes(df, {"code": "string", "missing": "float64"})
    assert list(result.columns) == ["code"]


def test_enforce_dtypes_rejects_unknown_kind():
    df = pd.DataFrame([{"code": "DE"}])
    with pytest.raises(ValueError, match="Unknown Gold dtype kind"):
        enforce_dtypes(df, {"code": "not-a-real-kind"})


def test_drop_missing_required_drops_rows_missing_any_required_column():
    df = enforce_dtypes(
        pd.DataFrame([
            {"code": "DE", "value": 1.0},
            {"code": None, "value": 2.0},
            {"code": "PL", "value": None},
        ]),
        {"code": "string", "value": "float64"},
    )

    result = drop_missing_required(df, ["code", "value"])

    assert list(result["code"]) == ["DE"]


def test_drop_missing_required_keeps_rows_missing_a_non_required_column():
    df = enforce_dtypes(
        pd.DataFrame([{"code": "DE", "optional": None}]),
        {"code": "string", "optional": "float64"},
    )

    result = drop_missing_required(df, ["code"])  # "optional" not required

    assert len(result) == 1


def test_drop_missing_required_treats_blank_string_as_missing():
    # A required string field that's empty or whitespace-only must be
    # dropped like a real NA — plain dropna() alone would keep it, since
    # "" and "   " aren't NaN.
    df = enforce_dtypes(
        pd.DataFrame([
            {"code": "DE"},
            {"code": ""},
            {"code": "   "},
        ]),
        {"code": "string"},
    )

    result = drop_missing_required(df, ["code"])

    assert list(result["code"]) == ["DE"]


def test_write_gold_table_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr("common.storage.PROJECT_ROOT", tmp_path)
    df = pd.DataFrame([{"country": "DE", "value": 1.0}])
    write_gold_table(df, "out/gold.parquet", "local")

    read_back = pd.read_parquet(tmp_path / "out/gold.parquet")
    assert read_back.to_dict("records") == df.to_dict("records")
