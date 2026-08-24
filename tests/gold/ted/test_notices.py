"""End-to-end test for gold/ted/notices.py: multiple transformed
per-country Parquet files -> one combined Gold file with the requested
columns and order (no renames for this source)."""
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
        "country_code", "publication_number", "publication_date", "contract_conclusion_date",
        "buyer_name", "total_value", "total_value_currency",
        "nuts", "nuts1", "nuts2", "nuts3", "nuts_label", "nuts1_label",
    ]
    # list-valued fields from normalization/transformation must not leak through
    assert "buyer_country" not in df.columns
    assert "classification_cpv" not in df.columns
    assert sorted(df["publication_number"]) == ["1-2025", "2-2025"]
    assert len(df) == 2  # both countries combined into one file
