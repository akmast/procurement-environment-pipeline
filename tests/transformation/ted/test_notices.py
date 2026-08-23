"""
transformation/ted/notices.py compatibility with normalization's list-typed
columns — full scenario: write a normalized Parquet file (the shape
normalization.ted.notices now actually produces), run transformation, read
the transformed Parquet back.

normalization.ted.notices keeps several fields as list[str], never a bare
scalar: buyer_country (a joint-procurement notice can name more than one
buyer country — this is the one that changed this session, see
normalization/ted/notices.py's unwrap_multi()), classification_cpv,
green_procurement_criteria, nuts_codes, place_of_performance_country.

CODELIST_JOINS used a plain Series.map() for buyer_country before this
fix, which can't match a list value against a scalar codelist key — it
silently produced a null label for every row instead of failing, which
is exactly the kind of bug that survives unnoticed without a test like
this one.
"""
import json
from io import BytesIO

import pandas as pd
import pytest

import normalization.ted.notices as ted_normalization
from transformation.ted.notices import run


def write_parquet(tmp_path, monkeypatch, relative_path, df):
    monkeypatch.setattr("common.storage.PROJECT_ROOT", tmp_path)
    full_path = tmp_path / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    buffer = BytesIO()
    df.to_parquet(buffer, index=False)
    full_path.write_bytes(buffer.getvalue())


def _base_row(publication_number, buyer_country, **overrides):
    """Every column add_codelist_labels()/deduplicate_notices() touches —
    CODELIST_JOINS' scalar fields plus all four list-valued fields —
    filled with innocuous placeholders so the join runs without a
    KeyError. Callers vary buyer_country (this test's focus) and
    whatever else via **overrides."""
    row = {
        "publication_number": publication_number,
        "notice_type": "can-standard",
        "total_value_currency": "EUR",
        "winner_selection_status": "comp-winner",
        "non_award_justification": None,
        "nuts": None, "nuts1": None, "nuts2": None, "nuts3": None,
        "classification_cpv": [],
        "place_of_performance_country": [],
        "green_procurement_criteria": [],
        "nuts_codes": [],
        "buyer_country": buyer_country,
    }
    row.update(overrides)
    return row


COUNTRY_CODELIST = pd.DataFrame([
    {"code": "DEU", "deu_label": "Deutschland"},
    {"code": "POL", "deu_label": "Polen"},
])


def _write_country_codelist(tmp_path, monkeypatch):
    write_parquet(tmp_path, monkeypatch, "data/normalized/ted/codelists/country.parquet", COUNTRY_CODELIST)


@pytest.mark.parametrize("buyer_country,expected_labels", [
    ([], []),
    (["DEU"], ["Deutschland"]),
    (["DEU", "POL"], ["Deutschland", "Polen"]),
])
def test_buyer_country_missing_single_multi_round_trip(tmp_path, monkeypatch, buyer_country, expected_labels):
    """Full scenario: normalized Parquet -> transformation -> read result
    Parquet, for each of missing/single/multi buyer_country."""
    notices = pd.DataFrame([_base_row("1-2025", buyer_country)])
    write_parquet(tmp_path, monkeypatch, "data/normalized/ted/DE/notices.parquet", notices)
    _write_country_codelist(tmp_path, monkeypatch)

    result = run(storage_mode="local", countries=["DE"])

    assert result.status != "FAILED"
    assert len(result.written_paths) == 1

    df = pd.read_parquet(tmp_path / result.written_paths[0])
    assert len(df) == 1
    row = df.iloc[0]

    # the original buyer_country column must survive untouched — full
    # list, in order, not collapsed to a single value or dropped
    assert list(row["buyer_country"]) == buyer_country
    assert list(row["buyer_country_labels"]) == expected_labels
    # the join must not have produced a stray scalar buyer_country_label column
    assert "buyer_country_label" not in df.columns


def test_multiple_notices_labels_preserve_order_per_row(tmp_path, monkeypatch):
    notices = pd.DataFrame([
        _base_row("1-2025", ["DEU"]),
        _base_row("2-2025", ["POL", "DEU"]),  # deliberately reversed order
        _base_row("3-2025", []),
    ])
    write_parquet(tmp_path, monkeypatch, "data/normalized/ted/DE/notices.parquet", notices)
    _write_country_codelist(tmp_path, monkeypatch)

    result = run(storage_mode="local", countries=["DE"])
    df = pd.read_parquet(tmp_path / result.written_paths[0])
    by_pub = df.set_index("publication_number")

    assert list(by_pub.loc["1-2025", "buyer_country_labels"]) == ["Deutschland"]
    assert list(by_pub.loc["2-2025", "buyer_country_labels"]) == ["Polen", "Deutschland"]
    assert list(by_pub.loc["3-2025", "buyer_country_labels"]) == []


def test_deduplication_preserves_list_columns_on_surviving_row(tmp_path, monkeypatch):
    """drop_duplicates(subset=["publication_number"]) must not choke on —
    or silently mangle — the other list-valued columns of the row it
    keeps."""
    notices = pd.DataFrame([
        _base_row("1-2025", ["DEU", "POL"], classification_cpv=["45000000"],
                  green_procurement_criteria=["other"], nuts_codes=["DE300"]),
        _base_row("1-2025", ["DEU", "POL"], classification_cpv=["45000000"],
                  green_procurement_criteria=["other"], nuts_codes=["DE300"]),  # exact duplicate
        _base_row("2-2025", ["POL"]),
    ])
    write_parquet(tmp_path, monkeypatch, "data/normalized/ted/DE/notices.parquet", notices)
    _write_country_codelist(tmp_path, monkeypatch)

    result = run(storage_mode="local", countries=["DE"])
    df = pd.read_parquet(tmp_path / result.written_paths[0])

    assert len(df) == 2  # the duplicate publication_number=1-2025 row was dropped
    row = df[df["publication_number"] == "1-2025"].iloc[0]
    assert list(row["buyer_country"]) == ["DEU", "POL"]
    assert list(row["classification_cpv"]) == ["45000000"]
    assert list(row["green_procurement_criteria"]) == ["other"]
    assert list(row["nuts_codes"]) == ["DE300"]


def test_other_list_columns_pass_through_untouched(tmp_path, monkeypatch):
    """classification_cpv, green_procurement_criteria and nuts_codes were
    already list-typed before this session's buyer_country fix — confirm
    transformation still carries them through unchanged (full values, in
    order, not collapsed) alongside the now-fixed buyer_country."""
    notices = pd.DataFrame([_base_row(
        "1-2025", ["DEU"],
        classification_cpv=["45000000", "45100000"],
        green_procurement_criteria=["other", "other"],
        nuts_codes=["DE3", "DE30", "DE300"],
    )])
    write_parquet(tmp_path, monkeypatch, "data/normalized/ted/DE/notices.parquet", notices)
    _write_country_codelist(tmp_path, monkeypatch)

    result = run(storage_mode="local", countries=["DE"])
    df = pd.read_parquet(tmp_path / result.written_paths[0])
    row = df.iloc[0]

    assert list(row["classification_cpv"]) == ["45000000", "45100000"]
    assert list(row["green_procurement_criteria"]) == ["other", "other"]
    assert list(row["nuts_codes"]) == ["DE3", "DE30", "DE300"]


def test_real_normalization_output_is_compatible_with_transformation(tmp_path, monkeypatch):
    """Strongest check: don't hand-build the normalized Parquet fixture —
    run the actual normalization.ted.notices.run() on raw TED-shaped JSON
    (missing / single / multi buyer-country notices) and feed its real
    output into transformation.ted.notices.run(), so a future schema
    drift between the two modules would fail here even if each module's
    own tests still pass in isolation."""
    monkeypatch.setattr("common.storage.PROJECT_ROOT", tmp_path)
    raw_notices = [
        {"publication-number": ["1-2025"], "notice-type": ["can-standard"]},  # no buyer-country
        {"publication-number": ["2-2025"], "notice-type": ["can-standard"], "buyer-country": ["DEU"]},
        {"publication-number": ["3-2025"], "notice-type": ["can-standard"],
         "buyer-country": ["DEU", "POL"]},
    ]
    raw_path = tmp_path / "data/raw/ted/DE/notices.jsonl"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text("\n".join(json.dumps(n) for n in raw_notices), encoding="utf-8")

    norm_result = ted_normalization.run(storage_mode="local", countries=["DE"])
    assert norm_result.status != "FAILED"

    _write_country_codelist(tmp_path, monkeypatch)
    transform_result = run(storage_mode="local", countries=["DE"])

    assert transform_result.status != "FAILED"
    assert len(transform_result.written_paths) == 1

    df = pd.read_parquet(tmp_path / transform_result.written_paths[0])
    by_pub = df.set_index("publication_number")
    assert list(by_pub.loc["1-2025", "buyer_country"]) == []
    assert list(by_pub.loc["2-2025", "buyer_country"]) == ["DEU"]
    assert list(by_pub.loc["3-2025", "buyer_country"]) == ["DEU", "POL"]
    assert list(by_pub.loc["2-2025", "buyer_country_labels"]) == ["Deutschland"]
    assert list(by_pub.loc["3-2025", "buyer_country_labels"]) == ["Deutschland", "Polen"]
