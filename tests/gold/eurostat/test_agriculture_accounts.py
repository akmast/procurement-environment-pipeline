"""End-to-end test for gold/eurostat/agriculture_accounts.py: multiple
normalized country/year Parquet files -> one combined Gold file with the
requested columns, order, and geo -> nuts2 rename."""
from io import BytesIO

import pandas as pd

from gold.eurostat.agriculture_accounts import GOLD_BASE_DIR, GOLD_FILENAME, run


def _write(tmp_path, monkeypatch, relative_path, df):
    monkeypatch.setattr("common.storage.PROJECT_ROOT", tmp_path)
    full_path = tmp_path / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    buffer = BytesIO()
    df.to_parquet(buffer, index=False)
    full_path.write_bytes(buffer.getvalue())


def _normalized_row(country, year, geo, value):
    return {
        "country_code": country, "freq": "A", "freq_label": "Annual",
        "am_item": "AM010000", "am_item_label": "Cereals",
        "indic_agr": "PRD_BP", "indic_agr_label": "Production value at basic price",
        "unit": "MIO_EUR", "unit_label": "Million euro",
        "geo": geo, "geo_label": "Some region",
        "time": year, "time_label": str(year), "value": value,
    }


def test_combines_countries_selects_renames_and_writes_one_file(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "data/normalized/eurostat/regional_agricultural_accounts/DE/2021/aact_eaa01_r.parquet",
           pd.DataFrame([_normalized_row("DE", 2021, "DE11", 273.94)]))
    _write(tmp_path, monkeypatch, "data/normalized/eurostat/regional_agricultural_accounts/PL/2021/aact_eaa01_r.parquet",
           pd.DataFrame([_normalized_row("PL", 2021, "PL22", 100.0)]))

    result = run(storage_mode="local", countries=["DE", "PL"])

    assert result.status != "FAILED"
    assert result.written_paths == [f"{GOLD_BASE_DIR}/{GOLD_FILENAME}"]

    df = pd.read_parquet(tmp_path / result.written_paths[0])
    assert list(df.columns) == [
        "country_code", "frequency_code", "frequency_label",
        "agricultural_item_code", "agricultural_item_label",
        "agricultural_indicator_code", "agricultural_indicator_label",
        "unit_label", "nuts2", "nuts2_label", "reference_year", "indicator_value",
    ]
    assert "unit" not in df.columns
    assert "time_label" not in df.columns
    assert "geo" not in df.columns  # renamed to nuts2
    assert "time" not in df.columns  # renamed to reference_year
    assert "value" not in df.columns  # renamed to indicator_value
    assert sorted(df["nuts2"]) == ["DE11", "PL22"]
    assert len(df) == 2  # both countries combined into one file
