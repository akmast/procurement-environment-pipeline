"""End-to-end test for gold/eurostat/agriculture_accounts.py: one
normalized country/year Parquet file -> one Gold partition file with
the requested columns, order, and geo -> nuts2 rename — never one
combined file for the whole source."""
from io import BytesIO

import pandas as pd

from common.storage import exists
from gold.eurostat.agriculture_accounts import GOLD_BASE_DIR, _LEGACY_GOLD_PATH, run


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


def test_writes_one_gold_file_per_country_year_partition_not_one_combined_file(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch,
           "data/normalized/eurostat/regional_agricultural_accounts/DE/2021/aact_eaa01_r.parquet",
           pd.DataFrame([_normalized_row("DE", 2021, "DE11", 273.94)]))
    _write(tmp_path, monkeypatch,
           "data/normalized/eurostat/regional_agricultural_accounts/PL/2021/aact_eaa01_r.parquet",
           pd.DataFrame([_normalized_row("PL", 2021, "PL22", 100.0)]))

    result = run(storage_mode="local", countries=["DE", "PL"])

    assert result.status != "FAILED"
    assert sorted(result.written_paths) == [
        f"{GOLD_BASE_DIR}/agriculture_accounts_DE_2021.parquet",
        f"{GOLD_BASE_DIR}/agriculture_accounts_PL_2021.parquet",
    ]

    de = pd.read_parquet(tmp_path / f"{GOLD_BASE_DIR}/agriculture_accounts_DE_2021.parquet")
    pl = pd.read_parquet(tmp_path / f"{GOLD_BASE_DIR}/agriculture_accounts_PL_2021.parquet")
    assert list(de.columns) == [
        "country_code", "frequency_code", "frequency_label",
        "agricultural_item_code", "agricultural_item_label",
        "agricultural_indicator_code", "agricultural_indicator_label",
        "unit_label", "nuts2", "nuts2_label", "reference_year", "indicator_value",
    ]
    assert "unit" not in de.columns
    assert "time_label" not in de.columns
    assert "geo" not in de.columns  # renamed to nuts2
    assert "time" not in de.columns  # renamed to reference_year
    assert "value" not in de.columns  # renamed to indicator_value
    assert len(de) == 1 and len(pl) == 1
    assert de["nuts2"].iloc[0] == "DE11"
    assert pl["nuts2"].iloc[0] == "PL22"


def test_rerunning_a_partition_overwrites_in_place_not_accumulates(tmp_path, monkeypatch):
    path = "data/normalized/eurostat/regional_agricultural_accounts/DE/2021/aact_eaa01_r.parquet"
    _write(tmp_path, monkeypatch, path, pd.DataFrame([_normalized_row("DE", 2021, "DE11", 273.94)]))
    run(storage_mode="local", countries=["DE"])

    _write(tmp_path, monkeypatch, path, pd.DataFrame([
        _normalized_row("DE", 2021, "DE11", 273.94),
        _normalized_row("DE", 2021, "DE12", 50.0),
    ]))
    result = run(storage_mode="local", countries=["DE"])

    out_path = f"{GOLD_BASE_DIR}/agriculture_accounts_DE_2021.parquet"
    assert result.written_paths == [out_path]
    df = pd.read_parquet(tmp_path / out_path)
    assert sorted(df["nuts2"]) == ["DE11", "DE12"]


def test_drops_rows_missing_any_required_field(tmp_path, monkeypatch):
    complete = _normalized_row("DE", 2021, "DE11", 273.94)
    missing_value = _normalized_row("DE", 2021, "DE12", None)  # indicator_value missing
    missing_label = _normalized_row("DE", 2021, "DE13", 50.0)
    missing_label["geo_label"] = "   "  # blank-only, must be treated as missing

    _write(tmp_path, monkeypatch,
           "data/normalized/eurostat/regional_agricultural_accounts/DE/2021/aact_eaa01_r.parquet",
           pd.DataFrame([complete, missing_value, missing_label]))

    result = run(storage_mode="local", countries=["DE"])

    df = pd.read_parquet(tmp_path / result.written_paths[0])
    assert sorted(df["nuts2"]) == ["DE11"]


def test_cleanup_legacy_file_only_when_requested(tmp_path, monkeypatch):
    monkeypatch.setattr("common.storage.PROJECT_ROOT", tmp_path)
    legacy_full_path = tmp_path / _LEGACY_GOLD_PATH
    legacy_full_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_full_path.write_bytes(b"old combined gold file")

    _write(tmp_path, monkeypatch,
           "data/normalized/eurostat/regional_agricultural_accounts/DE/2021/aact_eaa01_r.parquet",
           pd.DataFrame([_normalized_row("DE", 2021, "DE11", 273.94)]))

    run(storage_mode="local", countries=["DE"], cleanup_legacy_file=False)
    assert exists(_LEGACY_GOLD_PATH, "local")  # untouched on an ordinary incremental run

    run(storage_mode="local", countries=["DE"], cleanup_legacy_file=True)
    assert not exists(_LEGACY_GOLD_PATH, "local")  # removed on a --discover full rebuild
