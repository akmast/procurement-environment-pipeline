"""Unit tests for TED notices normalization's date and NUTS-code handling
(normalization/ted/notices.py) — regression coverage for:
- contract-conclusion-date arriving as a list of several dates instead
  of one string, which crashed date.fromisoformat() for the whole
  country.
- NUTS codes with a letter after the country prefix (e.g. "DED51",
  "PL22C") being misread as unrecognized.
"""
import logging
from datetime import date

import pytest

from normalization.ted.notices import (
    ISO3_PATTERN,
    NUTS_PATTERN,
    parse_ted_date,
    split_place_of_performance,
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
