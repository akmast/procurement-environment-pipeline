"""End-to-end test for gold/ted/notices.py: multiple transformed
per-country Parquet files -> one combined Gold file with the requested
columns, order, and renames."""
from io import BytesIO

import pandas as pd

from gold.ted.notices import GOLD_BASE_DIR, GOLD_FILENAME, run


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


def test_combines_countries_selects_columns_and_writes_one_file(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "data/transformed/ted/DE/notices.parquet",
           pd.DataFrame([_transformed_row("DE", "1-2025", 100000.0)]))
    _write(tmp_path, monkeypatch, "data/transformed/ted/PL/notices.parquet",
           pd.DataFrame([_transformed_row("PL", "2-2025", 50000.0)]))

    result = run(storage_mode="local", countries=["DE", "PL"])

    assert result.status != "FAILED"
    assert result.written_paths == [f"{GOLD_BASE_DIR}/{GOLD_FILENAME}"]

    df = pd.read_parquet(tmp_path / result.written_paths[0])
    assert list(df.columns) == [
        "country_code", "notice_publication_number", "notice_publication_date",
        "contract_conclusion_date", "buyer_name", "contract_total_value", "contract_currency_code",
        "place_of_performance_nuts", "nuts1", "nuts2", "nuts3",
        "place_of_performance_nuts_label", "nuts1_label",
    ]
    # list-valued fields from normalization/transformation must not leak through
    assert "buyer_country" not in df.columns
    assert "classification_cpv" not in df.columns
    assert "publication_number" not in df.columns  # renamed to notice_publication_number
    assert "nuts" not in df.columns  # renamed to place_of_performance_nuts
    assert sorted(df["notice_publication_number"]) == ["1-2025", "2-2025"]
    assert len(df) == 2  # both countries combined into one file


def test_keeps_rows_missing_value_or_currency_but_drops_rows_missing_identity(tmp_path, monkeypatch):
    complete = _transformed_row("DE", "1-2025", 100000.0)
    missing_value = _transformed_row("DE", "2-2025", None)
    missing_value["total_value_currency"] = None  # both missing together, still kept
    missing_identity = _transformed_row("DE", None, 50000.0)  # no publication_number

    _write(tmp_path, monkeypatch, "data/transformed/ted/DE/notices.parquet",
           pd.DataFrame([complete, missing_value, missing_identity]))

    result = run(storage_mode="local", countries=["DE"])

    df = pd.read_parquet(tmp_path / result.written_paths[0])
    # missing_identity dropped; missing_value kept (contract_total_value/
    # contract_currency_code are deliberately not required — a notice
    # still counts even with an unknown value)
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
    # exact type check, not isinstance: datetime.datetime/pd.Timestamp are
    # themselves subclasses of datetime.date, so isinstance() alone
    # wouldn't catch a column that stayed a full timestamp
    assert type(df["notice_publication_date"].iloc[0]) is datetime.date
