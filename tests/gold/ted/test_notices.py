"""End-to-end test for gold/ted/notices.py: one transformed per-country
Parquet file -> one Gold partition file with the requested columns,
order, and renames — never one combined file for the whole source."""
from io import BytesIO

import pandas as pd

from common.storage import exists
from gold.ted.notices import GOLD_BASE_DIR, _LEGACY_GOLD_PATH, run


def _write(tmp_path, monkeypatch, relative_path, df):
    monkeypatch.setattr("common.storage.PROJECT_ROOT", tmp_path)
    full_path = tmp_path / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    buffer = BytesIO()
    df.to_parquet(buffer, index=False)
    full_path.write_bytes(buffer.getvalue())


def _transformed_row(country, publication_number, total_value):
    return {
        "country_code": country, "publication_number": publication_number,
        "notice_type": "can-standard", "notice_type_label": "Contract award notice",
        "publication_date": "2025-01-01", "contract_conclusion_date": "2025-01-15",
        "buyer_name": "Some Buyer", "buyer_country": ["DEU"], "buyer_country_labels": ["Deutschland"],
        "total_value": total_value, "total_value_currency": "EUR",
        "nuts": "DE300", "nuts1": "DE3", "nuts2": "DE30", "nuts3": "DE300",
        "nuts_label": "Berlin", "nuts1_label": "Berlin-region",
        "classification_cpv": [], "classification_cpv_labels": [],
        "place_of_performance_country": [], "place_of_performance_country_labels": [],
    }


def test_writes_one_gold_file_per_country_partition_not_one_combined_file(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "data/transformed/ted/DE/notices.parquet",
           pd.DataFrame([_transformed_row("DE", "1-2025", 100000.0)]))
    _write(tmp_path, monkeypatch, "data/transformed/ted/PL/notices.parquet",
           pd.DataFrame([_transformed_row("PL", "2-2025", 50000.0)]))

    result = run(storage_mode="local", countries=["DE", "PL"])

    assert result.status != "FAILED"
    assert sorted(result.written_paths) == [
        f"{GOLD_BASE_DIR}/notices_DE.parquet",
        f"{GOLD_BASE_DIR}/notices_PL.parquet",
    ]

    de = pd.read_parquet(tmp_path / f"{GOLD_BASE_DIR}/notices_DE.parquet")
    pl = pd.read_parquet(tmp_path / f"{GOLD_BASE_DIR}/notices_PL.parquet")
    assert list(de.columns) == [
        "country_code", "notice_publication_number", "notice_publication_date",
        "contract_conclusion_date", "buyer_name", "contract_total_value", "contract_currency_code",
        "place_of_performance_nuts", "nuts1", "nuts2", "nuts3",
        "place_of_performance_nuts_label", "nuts1_label",
    ]
    assert "buyer_country" not in de.columns
    assert "classification_cpv" not in de.columns
    assert "publication_number" not in de.columns
    assert "nuts" not in de.columns
    assert len(de) == 1 and len(pl) == 1
    assert de["notice_publication_number"].iloc[0] == "1-2025"
    assert pl["notice_publication_number"].iloc[0] == "2-2025"


def test_rerunning_a_country_overwrites_in_place_not_accumulates(tmp_path, monkeypatch):
    # TED's transformation stage always rewrites a touched country's
    # ENTIRE notice history in one file, not just new notices — so this
    # is the case where an append-only Gold write would double-count
    # every previously-seen notice. Overwriting the country's Gold file
    # in place on every run is what keeps that from happening.
    path = "data/transformed/ted/DE/notices.parquet"
    _write(tmp_path, monkeypatch, path,
           pd.DataFrame([_transformed_row("DE", "1-2025", 100000.0)]))
    run(storage_mode="local", countries=["DE"])

    _write(tmp_path, monkeypatch, path, pd.DataFrame([
        _transformed_row("DE", "1-2025", 100000.0),
        _transformed_row("DE", "2-2025", 50000.0),
    ]))
    result = run(storage_mode="local", countries=["DE"])

    out_path = f"{GOLD_BASE_DIR}/notices_DE.parquet"
    assert result.written_paths == [out_path]
    df = pd.read_parquet(tmp_path / out_path)
    assert sorted(df["notice_publication_number"]) == ["1-2025", "2-2025"]


def test_keeps_rows_missing_value_or_currency_but_drops_rows_missing_identity(tmp_path, monkeypatch):
    complete = _transformed_row("DE", "1-2025", 100000.0)
    missing_value = _transformed_row("DE", "2-2025", None)
    missing_value["total_value_currency"] = None
    missing_identity = _transformed_row("DE", None, 50000.0)

    _write(tmp_path, monkeypatch, "data/transformed/ted/DE/notices.parquet",
           pd.DataFrame([complete, missing_value, missing_identity]))

    result = run(storage_mode="local", countries=["DE"])

    df = pd.read_parquet(tmp_path / result.written_paths[0])
    assert sorted(df["notice_publication_number"]) == ["1-2025", "2-2025"]
    by_number = df.set_index("notice_publication_number")
    assert pd.isna(by_number.loc["2-2025", "contract_total_value"])
    assert pd.isna(by_number.loc["2-2025", "contract_currency_code"])


def test_dates_are_calendar_dates_not_timestamps(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "data/transformed/ted/DE/notices.parquet",
           pd.DataFrame([_transformed_row("DE", "1-2025", 100000.0)]))

    result = run(storage_mode="local", countries=["DE"])

    df = pd.read_parquet(tmp_path / result.written_paths[0])
    import datetime
    assert type(df["notice_publication_date"].iloc[0]) is datetime.date


def test_cleanup_legacy_file_only_when_requested(tmp_path, monkeypatch):
    monkeypatch.setattr("common.storage.PROJECT_ROOT", tmp_path)
    legacy_full_path = tmp_path / _LEGACY_GOLD_PATH
    legacy_full_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_full_path.write_bytes(b"old combined gold file")

    _write(tmp_path, monkeypatch, "data/transformed/ted/DE/notices.parquet",
           pd.DataFrame([_transformed_row("DE", "1-2025", 100000.0)]))

    run(storage_mode="local", countries=["DE"], cleanup_legacy_file=False)
    assert exists(_LEGACY_GOLD_PATH, "local")  # untouched on an ordinary incremental run

    run(storage_mode="local", countries=["DE"], cleanup_legacy_file=True)
    assert not exists(_LEGACY_GOLD_PATH, "local")  # removed on a --discover full rebuild
