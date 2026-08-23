"""Unit tests for TED notices normalization's date and NUTS-code handling
(normalization/ted/notices.py) — regression coverage for:
- contract-conclusion-date arriving as a list of several dates instead
  of one string, which crashed date.fromisoformat() for the whole
  country.
- NUTS codes with a letter after the country prefix (e.g. "DED51",
  "PL22C") being misread as unrecognized.
- a multi-element buyer-country list left as a raw Python list in what
  pyarrow expected to be a plain string column ("ArrowTypeError:
  Expected bytes, got a 'list' object") — every column must hold one
  stable type across all rows.
"""
import json
import logging
from datetime import date

import pandas as pd
import pytest

from normalization.ted.notices import (
    ISO3_PATTERN,
    LIST_COLUMNS,
    NOTICES_RAW_FILENAME,
    NUTS_PATTERN,
    RAW_BASE_DIR,
    flatten_notice,
    parse_ted_date,
    run,
    split_place_of_performance,
    unwrap_multi,
    unwrap_required_scalar,
    validate_column_types,
)


class TestParseTedDate:
    def test_single_string(self):
        assert parse_ted_date("2025-12-31+01:00") == date(2025, 12, 31)

    def test_single_element_list(self):
        assert parse_ted_date(["2025-12-31+01:00"]) == date(2025, 12, 31)

    def test_multiple_dates_picks_earliest(self):
        value = ["2025-12-31+01:00", "2024-06-15+02:00", "2025-01-01+00:00"]
        assert parse_ted_date(value) == date(2024, 6, 15)

    def test_none(self):
        assert parse_ted_date(None) is None

    def test_empty_list(self):
        assert parse_ted_date([]) is None

    def test_empty_string(self):
        assert parse_ted_date("") is None

    def test_malformed_string_logs_and_returns_none(self, caplog):
        with caplog.at_level(logging.WARNING):
            assert parse_ted_date("not-a-date") is None
        assert "Could not parse TED date" in caplog.text

    def test_list_with_some_malformed_dates_uses_valid_ones(self, caplog):
        value = ["not-a-date", "2024-06-15+02:00"]
        with caplog.at_level(logging.WARNING):
            assert parse_ted_date(value) == date(2024, 6, 15)
        assert "Could not parse TED date" in caplog.text

    def test_list_all_malformed_returns_none(self, caplog):
        with caplog.at_level(logging.WARNING):
            assert parse_ted_date(["not-a-date", "also-bad"]) is None

    def test_unexpected_scalar_type_logs_and_returns_none(self, caplog):
        with caplog.at_level(logging.WARNING):
            assert parse_ted_date(20251231) is None
        assert "Unexpected TED date type" in caplog.text

    def test_unexpected_dict_type_logs_and_returns_none(self, caplog):
        with caplog.at_level(logging.WARNING):
            assert parse_ted_date({"date": "2025-12-31"}) is None
        assert "Unexpected TED date type" in caplog.text

    def test_list_containing_an_unexpected_type_is_skipped(self, caplog):
        value = [123, "2024-06-15+02:00"]
        with caplog.at_level(logging.WARNING):
            assert parse_ted_date(value) == date(2024, 6, 15)


class TestNutsPattern:
    @pytest.mark.parametrize(
        "code",
        ["DE7", "DE71", "DE712", "DED5", "DED51", "PL22C", "DEA"],
    )
    def test_matches_valid_nuts_codes(self, code):
        assert NUTS_PATTERN.match(code)

    @pytest.mark.parametrize("code", ["D712", "DE71234", "de712", ""])
    def test_does_not_match_malformed_codes(self, code):
        assert NUTS_PATTERN.match(code) is None


class TestSplitPlaceOfPerformance:
    def test_letter_suffixed_nuts_codes_are_recognized(self):
        nuts_codes, country_codes = split_place_of_performance(["DED51", "PL22C"])
        assert nuts_codes == ["DED51", "PL22C"]
        assert country_codes == []

    def test_digit_only_nuts_code_still_recognized(self):
        nuts_codes, country_codes = split_place_of_performance(["DE712"])
        assert nuts_codes == ["DE712"]
        assert country_codes == []

    def test_iso3_country_code_not_misread_as_nuts(self):
        # "DEU" has the same 2-letters-plus-1-char shape as a bare
        # letter-only NUTS1 code — ISO3_PATTERN is checked first so
        # this stays a country code, matching real ingestion output
        # (e.g. ["DE236", "DEU"]).
        assert ISO3_PATTERN.match("DEU")
        nuts_codes, country_codes = split_place_of_performance(["DE236", "DEU"])
        assert nuts_codes == ["DE236"]
        assert country_codes == ["DEU"]

    def test_unrecognized_value_is_logged_and_dropped(self, caplog):
        with caplog.at_level(logging.WARNING):
            nuts_codes, country_codes = split_place_of_performance(["not-a-code"])
        assert nuts_codes == []
        assert country_codes == []
        assert "Unrecognized place-of-performance value" in caplog.text


# --------------------------------------------------------------------------
# Field type stability — missing / scalar / single-element / multi-element,
# for both the "must always be scalar" and "must always be a list" helpers.
# --------------------------------------------------------------------------

class TestUnwrapRequiredScalar:
    @pytest.mark.parametrize("raw,expected", [
        (None, None),
        ([], None),
        ("can-standard", "can-standard"),          # bare scalar (defensive; not TED's normal shape)
        (["can-standard"], "can-standard"),        # single-element list — TED's normal shape
    ])
    def test_missing_scalar_and_single_element(self, raw, expected):
        assert unwrap_required_scalar(raw, "notice_type") == expected

    def test_multi_element_is_joined_and_logged_not_silently_truncated(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = unwrap_required_scalar(["can-standard", "can-social"], "notice_type")
        assert result == "can-standard; can-social"
        assert "Expected a single value for notice_type" in caplog.text


class TestUnwrapMulti:
    @pytest.mark.parametrize("raw,expected", [
        (None, []),
        ([], []),
        ("DEU", ["DEU"]),           # bare scalar (defensive)
        (["DEU"], ["DEU"]),         # single-element list — TED's usual shape
        (["DEU", "POL"], ["DEU", "POL"]),  # genuine multi-value (joint procurement)
    ])
    def test_missing_scalar_single_and_multi_element(self, raw, expected):
        assert unwrap_multi(raw) == expected


class TestFlattenNoticeBuyerCountry:
    @pytest.mark.parametrize("raw,expected", [
        (None, []),
        (["DEU"], ["DEU"]),
        (["DEU", "POL"], ["DEU", "POL"]),
    ])
    def test_buyer_country_is_always_a_list(self, raw, expected):
        notice = {"publication-number": ["1-2025"]} if raw is None else {
            "publication-number": ["1-2025"], "buyer-country": raw,
        }
        row = flatten_notice(notice, "DE")
        assert row["buyer_country"] == expected
        assert isinstance(row["buyer_country"], list)

    def test_other_fields_stay_scalar_even_when_wrapped(self):
        notice = {
            "publication-number": ["1-2025"],
            "notice-type": ["can-standard"],
            "buyer-post-code": ["10115"],
            "winner-selection-status": ["comp-winner"],
            "total-value-cur": ["EUR"],
        }
        row = flatten_notice(notice, "DE")
        for field in ["publication_number", "notice_type", "buyer_post_code",
                       "winner_selection_status", "total_value_currency"]:
            assert not isinstance(row[field], list), f"{field} must not be a list"
        assert row["publication_number"] == "1-2025"
        assert row["notice_type"] == "can-standard"


class TestValidateColumnTypes:
    def test_passes_on_well_typed_dataframe(self):
        df = pd.DataFrame([
            {"publication_number": "1-2025", "buyer_country": ["DEU"]},
            {"publication_number": "2-2025", "buyer_country": ["DEU", "POL"]},
            {"publication_number": "3-2025", "buyer_country": []},
        ])
        # only buyer_country is a real LIST_COLUMNS member here; treat
        # publication_number as the representative "must stay scalar" column
        validate_column_types(df[["publication_number", "buyer_country"]])

    def test_raises_naming_column_and_type_for_list_in_scalar_column(self):
        df = pd.DataFrame([{"publication_number": "1-2025"}, {"publication_number": ["1-2025", "2-2025"]}])
        with pytest.raises(TypeError, match="publication_number.*list"):
            validate_column_types(df)

    def test_raises_naming_column_and_type_for_scalar_in_list_column(self):
        df = pd.DataFrame([{"buyer_country": ["DEU"]}, {"buyer_country": "DEU"}])
        with pytest.raises(TypeError, match="buyer_country.*str"):
            validate_column_types(df)

    def test_list_columns_constant_matches_known_fields(self):
        assert LIST_COLUMNS == {
            "buyer_country", "classification_cpv", "green_procurement_criteria",
            "nuts_codes", "place_of_performance_country",
        }


# --------------------------------------------------------------------------
# End-to-end: run() writes and reads back a real Parquet file without
# ArrowTypeError, for the exact shapes that used to crash it.
# --------------------------------------------------------------------------

def write_raw_notices(tmp_path, monkeypatch, notices, country="DE"):
    monkeypatch.setattr("common.storage.PROJECT_ROOT", tmp_path)
    raw_path = f"{RAW_BASE_DIR}/{country}/{NOTICES_RAW_FILENAME}"
    full_path = tmp_path / raw_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text("\n".join(json.dumps(n) for n in notices), encoding="utf-8")
    return raw_path


class TestRunWritesAndReadsParquet:
    def test_mixed_missing_single_and_multi_buyer_country_round_trips(self, tmp_path, monkeypatch):
        notices = [
            {"publication-number": ["1-2025"], "notice-type": ["can-standard"]},  # missing buyer-country
            {"publication-number": ["2-2025"], "notice-type": ["can-standard"], "buyer-country": ["DEU"]},
            {"publication-number": ["3-2025"], "notice-type": ["can-standard"],
             "buyer-country": ["DEU", "POL"]},
        ]
        write_raw_notices(tmp_path, monkeypatch, notices)

        result = run(storage_mode="local", countries=["DE"])

        assert result.status != "FAILED"
        assert result.failed_paths == []
        assert len(result.written_paths) == 1

        written = tmp_path / result.written_paths[0]
        assert written.exists()
        df = pd.read_parquet(written)
        assert len(df) == 3
        by_pub = df.set_index("publication_number")
        assert list(by_pub.loc["1-2025", "buyer_country"]) == []
        assert list(by_pub.loc["2-2025", "buyer_country"]) == ["DEU"]
        assert list(by_pub.loc["3-2025", "buyer_country"]) == ["DEU", "POL"]
