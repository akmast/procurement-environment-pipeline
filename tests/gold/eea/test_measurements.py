"""End-to-end test for gold/eea/measurements.py: multiple transformed
country/year/pollutant Parquet files -> one combined Gold file with the
requested columns, order, and nuts*_code -> nuts* renames."""
from io import BytesIO

import pandas as pd

from gold.eea.measurements import GOLD_BASE_DIR, GOLD_FILENAME, run


def _write(tmp_path, monkeypatch, relative_path, df):
    monkeypatch.setattr("common.storage.PROJECT_ROOT", tmp_path)
    full_path = tmp_path / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    buffer = BytesIO()
    df.to_parquet(buffer, index=False)
    full_path.write_bytes(buffer.getvalue())


def _transformed_row(country, sampling_point, value, nuts1, nuts2, nuts3):
    return {
        "country_code": country, "sampling_point": sampling_point, "pollutant": "PM10",
        "period_start": "2021-01-01T00:00:00", "period_end": "2021-01-01T01:00:00",
        "value": value, "unit": "ug/m3", "aggregation_type": "hour",
        "validity": 1, "verification": 1, "result_time": "2021-01-01T02:00:00",
        "location": "Berlin", "nuts1_code": nuts1, "nuts2_code": nuts2, "nuts3_code": nuts3,
    }


def test_combines_countries_selects_renames_and_writes_one_file(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch,
           "data/transformed/eea/measurements/DE/2021/PM10/measurements.parquet",
           pd.DataFrame([_transformed_row(
               "DE", "SPO.DE_DEBE034_PM1_dataGroup2", 10.5, "DE3", "DE30", "DE300")]))
    _write(tmp_path, monkeypatch,
           "data/transformed/eea/measurements/PL/2021/PM10/measurements.parquet",
           pd.DataFrame([_transformed_row(
               "PL", "SPO.PL_PL0001A_PM1_dataGroup2", 20.5, "PL2", "PL22", "PL227")]))

    result = run(storage_mode="local", countries=["DE", "PL"])

    assert result.status != "FAILED"
    assert result.written_paths == [f"{GOLD_BASE_DIR}/{GOLD_FILENAME}"]

    df = pd.read_parquet(tmp_path / result.written_paths[0])
    assert list(df.columns) == [
        "country_code", "sampling_point_id", "pollutant_code",
        "measurement_period_start", "measurement_period_end",
        "measurement_value", "measurement_unit", "validity_code", "verification_code",
        "result_timestamp", "station_location", "nuts1", "nuts2", "nuts3",
    ]
    assert "aggregation_type" not in df.columns
    assert "nuts1_code" not in df.columns  # renamed to nuts1
    assert "sampling_point" not in df.columns  # renamed to sampling_point_id
    assert "value" not in df.columns  # renamed to measurement_value
    assert sorted(df["nuts2"]) == ["DE30", "PL22"]
    assert len(df) == 2  # both countries combined into one file


def test_pollutant_code_is_a_string_even_when_source_stores_it_numeric(tmp_path, monkeypatch):
    # pollutant_code is an EEA vocabulary code, never an arithmetic
    # value — the source partition file storing it as a plain int
    # (as older transformed data does) must not leak through as a Glue
    # `bigint`-incompatible type; enforce_dtypes() always casts it to
    # a real string.
    row = _transformed_row("DE", "SPO.DE_DEBE034_PM1_dataGroup2", 10.5, "DE3", "DE30", "DE300")
    row["pollutant"] = 5  # numeric, like the legacy Int64-cast data
    _write(tmp_path, monkeypatch,
           "data/transformed/eea/measurements/DE/2021/PM10/measurements.parquet",
           pd.DataFrame([row]))

    result = run(storage_mode="local", countries=["DE"])

    df = pd.read_parquet(tmp_path / result.written_paths[0])
    assert df["pollutant_code"].dtype == "string"
    assert df["pollutant_code"].iloc[0] == "5"


def test_drops_rows_missing_a_required_field_but_keeps_missing_verification(tmp_path, monkeypatch):
    complete = _transformed_row("DE", "SPO.DE_DEBE034_PM1_dataGroup2", 10.5, "DE3", "DE30", "DE300")
    missing_validity = _transformed_row("DE", "SPO.DE_DEBE035_PM1_dataGroup2", 11.0, "DE3", "DE30", "DE300")
    missing_validity["validity"] = None
    missing_verification = _transformed_row("DE", "SPO.DE_DEBE036_PM1_dataGroup2", 12.0, "DE3", "DE30", "DE300")
    missing_verification["verification"] = None

    _write(tmp_path, monkeypatch,
           "data/transformed/eea/measurements/DE/2021/PM10/measurements.parquet",
           pd.DataFrame([complete, missing_validity, missing_verification]))

    result = run(storage_mode="local", countries=["DE"])

    df = pd.read_parquet(tmp_path / result.written_paths[0])
    # missing_validity dropped (validity_code is required); missing_verification kept
    assert sorted(df["sampling_point_id"]) == [
        "SPO.DE_DEBE034_PM1_dataGroup2", "SPO.DE_DEBE036_PM1_dataGroup2",
    ]
