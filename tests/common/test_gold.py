"""Unit tests for common/gold.py's build_gold_partition()/
gold_partition_path()/enforce_dtypes()/drop_missing_required()/
write_gold_table() — the shared select+order+rename+dedup+cast+
null-guard+write-per-partition logic every gold/<source>/*.py module
relies on."""
import logging
from io import BytesIO

import pandas as pd
import pytest

from common.gold import build_gold_partition, drop_missing_required, enforce_dtypes, gold_partition_path, \
    write_gold_table


def _write(tmp_path, monkeypatch, relative_path, df):
    monkeypatch.setattr("common.storage.PROJECT_ROOT", tmp_path)
    full_path = tmp_path / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    buffer = BytesIO()
    df.to_parquet(buffer, index=False)
    full_path.write_bytes(buffer.getvalue())


def test_build_gold_partition_selects_orders_and_renames(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "a.parquet",
           pd.DataFrame([{"code": "DE", "value": 1.0, "extra": "x"}]))

    df = build_gold_partition("a.parquet", "local", ["code", "value"], rename={"code": "country"})

    assert list(df.columns) == ["country", "value"]
    assert df["country"].iloc[0] == "DE"
    assert "extra" not in df.columns


def test_build_gold_partition_deduplicates_exact_repeat_rows(tmp_path, monkeypatch):
    row = {"code": "DE", "value": 1.0}
    _write(tmp_path, monkeypatch, "a.parquet", pd.DataFrame([row, row]))  # exact duplicate within one file

    df = build_gold_partition("a.parquet", "local", ["code", "value"])

    assert len(df) == 1


def test_build_gold_partition_missing_column_logs_and_fills_nan_instead_of_raising(tmp_path, monkeypatch, caplog):
    _write(tmp_path, monkeypatch, "a.parquet", pd.DataFrame([{"code": "PL"}]))  # no "value" column

    with caplog.at_level(logging.WARNING):
        df = build_gold_partition("a.parquet", "local", ["code", "value"])

    assert "missing expected column" in caplog.text
    assert pd.isna(df.set_index("code").loc["PL", "value"])


def test_gold_partition_path_mirrors_precursor_partition_segments():
    # EEA: country/year/pollutant
    assert gold_partition_path(
        "data/transformed/eea/measurements/DE/2021/PM10/measurements.parquet",
        "data/transformed/eea/measurements", "data/gold/eea", "measurements",
    ) == "data/gold/eea/measurements_DE_2021_PM10.parquet"

    # TED: country only
    assert gold_partition_path(
        "data/transformed/ted/DE/notices.parquet",
        "data/transformed/ted", "data/gold/ted", "notices",
    ) == "data/gold/ted/notices_DE.parquet"

    # Eurostat: country/year
    assert gold_partition_path(
        "data/normalized/eurostat/regional_agricultural_accounts/DE/2021/aact_eaa01_r.parquet",
        "data/normalized/eurostat/regional_agricultural_accounts", "data/gold/eurostat", "agriculture_accounts",
    ) == "data/gold/eurostat/agriculture_accounts_DE_2021.parquet"


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
