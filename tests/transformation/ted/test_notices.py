"""
Compact regression test for transformation/ted/notices.py's buyer_country
codelist join.

normalization.ted.notices now keeps buyer_country as a list (a
joint-procurement notice can name more than one buyer country) instead
of a scalar — the old CODELIST_JOINS entry ("buyer_country", "country",
"buyer_country_label") used a plain Series.map(), which can't match a
list value against a scalar codelist key and would silently produce
null labels for every row. This checks the fix: buyer_country is joined
the same list-per-code way as classification_cpv/
place_of_performance_country, producing buyer_country_labels.
"""
from io import BytesIO

import pandas as pd

from transformation.ted.notices import run


def write_parquet(tmp_path, monkeypatch, relative_path, df):
    monkeypatch.setattr("common.storage.PROJECT_ROOT", tmp_path)
    full_path = tmp_path / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    buffer = BytesIO()
    df.to_parquet(buffer, index=False)
    full_path.write_bytes(buffer.getvalue())


def _base_row(publication_number, buyer_country):
    """All the columns add_codelist_labels() touches — CODELIST_JOINS'
    scalar fields plus the three list-join fields — filled with
    innocuous placeholders so the join runs without a KeyError; only
    buyer_country is what this test actually varies."""
    return {
        "publication_number": publication_number,
        "notice_type": "can-standard",
        "total_value_currency": "EUR",
        "winner_selection_status": "comp-winner",
        "non_award_justification": None,
        "nuts": None, "nuts1": None, "nuts2": None, "nuts3": None,
        "classification_cpv": [],
        "place_of_performance_country": [],
        "buyer_country": buyer_country,
    }


def test_buyer_country_labels_join_for_single_and_multi_value_lists(tmp_path, monkeypatch):
    notices = pd.DataFrame([
        _base_row("1-2025", ["DEU"]),
        _base_row("2-2025", ["DEU", "POL"]),
        _base_row("3-2025", []),
    ])
    write_parquet(tmp_path, monkeypatch, "data/normalized/ted/DE/notices.parquet", notices)

    country_codelist = pd.DataFrame([
        {"code": "DEU", "deu_label": "Deutschland"},
        {"code": "POL", "deu_label": "Polen"},
    ])
    write_parquet(tmp_path, monkeypatch, "data/normalized/ted/codelists/country.parquet", country_codelist)

    result = run(storage_mode="local", countries=["DE"])

    assert result.status != "FAILED"
    assert len(result.written_paths) == 1

    df = pd.read_parquet(tmp_path / result.written_paths[0])
    by_pub = df.set_index("publication_number")
    assert list(by_pub.loc["1-2025", "buyer_country_labels"]) == ["Deutschland"]
    assert list(by_pub.loc["2-2025", "buyer_country_labels"]) == ["Deutschland", "Polen"]
    assert list(by_pub.loc["3-2025", "buyer_country_labels"]) == []
    # the join must not have produced a stray scalar buyer_country_label column
    assert "buyer_country_label" not in df.columns
