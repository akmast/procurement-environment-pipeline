"""
Regression tests for normalization/eurostat/agriculture_accounts.py's
JSON-stat 2.0 handling — covers the production bug where a structurally
valid but empty Eurostat response (a requested year not published yet,
e.g. Poland/2025) crashed cast_types() with `KeyError: 'value'` because
melt_json_stat() had returned a columnless empty DataFrame.

Four groups, matching the task's request not to test every small check
in isolation:
  1. a small filled cube — flat-index decoding, codes/labels, 0.0 and
     negative values preserved, dtypes.
  2. valid empty responses (three shapes) — NO_DATA handled without
     ever reaching cast_types(), no parquet written, file not failed.
  3. individual observation value types — what coerces to float vs what
     must fail loudly instead of silently becoming NaN/1.0/0.0.
  4. corrupted JSON-stat structure — real errors, land in failed_paths.
"""
import json
import logging

import pandas as pd
import pytest

from normalization.eurostat import agriculture_accounts as mod


# --------------------------------------------------------------------------
# A small, fully worked-out synthetic cube — id/size chosen small enough to
# hand-decode, but exercising the same shape as the real dataset (6 dims,
# one of them "time", object-form category.index deliberately out of key
# order to prove decoding sorts by position, not dict iteration order).
#
# size = [1, 2, 2, 1, 2, 1] -> total cells = 8, strides = [8, 4, 2, 2, 1, 1]
# value = {"0": 10.5, "1": 20.5, "2": 0.0, "7": -3.5} decodes to:
#   0 -> freq=A am_item=AM010000 indic_agr=PRD_BP unit=MIO_EUR geo=DE11 time=2021 -> 10.5
#   1 -> freq=A am_item=AM010000 indic_agr=PRD_BP unit=MIO_EUR geo=DE12 time=2021 -> 20.5
#   2 -> freq=A am_item=AM010000 indic_agr=PRD_PP unit=MIO_EUR geo=DE11 time=2021 -> 0.0
#   7 -> freq=A am_item=AM011000 indic_agr=PRD_PP unit=MIO_EUR geo=DE12 time=2021 -> -3.5
# --------------------------------------------------------------------------

def make_filled_payload() -> dict:
    return {
        "class": "dataset",
        "id": ["freq", "am_item", "indic_agr", "unit", "geo", "time"],
        "size": [1, 2, 2, 1, 2, 1],
        "dimension": {
            "freq": {"category": {"index": {"A": 0}, "label": {"A": "Annual"}}},
            "am_item": {
                "category": {
                    # deliberately out of key order vs. position, to prove
                    # category_codes() sorts by position value
                    "index": {"AM011000": 1, "AM010000": 0},
                    "label": {"AM010000": "Cereals (including seeds)", "AM011000": "Wheat and spelt"},
                }
            },
            "indic_agr": {
                "category": {
                    "index": {"PRD_BP": 0, "PRD_PP": 1},
                    "label": {
                        "PRD_BP": "Production value at basic price",
                        "PRD_PP": "Production value at producer price",
                    },
                }
            },
            "unit": {"category": {"index": {"MIO_EUR": 0}, "label": {"MIO_EUR": "Million euro"}}},
            "geo": {
                "category": {
                    "index": {"DE11": 0, "DE12": 1},
                    "label": {"DE11": "Stuttgart", "DE12": "Karlsruhe"},
                }
            },
            "time": {"category": {"index": {"2021": 0}, "label": {"2021": "2021"}}},
        },
        "value": {"0": 10.5, "1": 20.5, "2": 0.0, "7": -3.5},
    }


def make_empty_year_payload() -> dict:
    """Poland/2025 shape: time's own size is 0, so the cube's total size is
    0 too — the requested year simply isn't published yet."""
    payload = make_filled_payload()
    payload["size"] = [1, 2, 2, 1, 2, 0]
    payload["dimension"]["time"] = {"category": {"index": {}, "label": {}}}
    payload["value"] = {}
    return payload


def make_sparse_no_value_payload() -> dict:
    """Cube size is non-zero, but every cell is either unset or status-only
    — a legitimate sparse NO_DATA response, not an error."""
    payload = make_filled_payload()
    payload["value"] = {}
    return payload


def make_status_only_payload() -> dict:
    """Same as sparse-no-value, but with status flags present — status
    keys never overlap with value's keys, so this must not be mistaken
    for having observations either."""
    payload = make_sparse_no_value_payload()
    payload["status"] = {"0": "m", "1": "m"}
    return payload


def write_raw_file(tmp_path, monkeypatch, payload, country="PL", year="2025"):
    """Points common.storage's local-mode root at tmp_path and writes one
    raw JSON-stat file at the normal <country>/<year>/... partition path,
    so run()/normalize_file() can be exercised end-to-end without ever
    touching the real repo's data/ directory."""
    monkeypatch.setattr("common.storage.PROJECT_ROOT", tmp_path)
    raw_path = f"{mod.RAW_BASE_DIR}/{country}/{year}/{mod.RAW_FILENAME}"
    full_path = tmp_path / raw_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(json.dumps(payload), encoding="utf-8")
    return raw_path


# --------------------------------------------------------------------------
# 1. Small filled cube
# --------------------------------------------------------------------------

class TestFilledCube:
    def test_melt_decodes_flat_indices_correctly(self):
        df = mod.melt_json_stat(make_filled_payload(), country="DE")

        assert len(df) == 4
        by_value = {row["value"]: row for row in df.to_dict("records")}

        assert by_value[10.5]["geo"] == "DE11"
        assert by_value[10.5]["geo_label"] == "Stuttgart"
        assert by_value[10.5]["am_item"] == "AM010000"
        assert by_value[10.5]["am_item_label"] == "Cereals (including seeds)"
        assert by_value[10.5]["indic_agr"] == "PRD_BP"
        assert by_value[10.5]["time"] == "2021"

        assert by_value[20.5]["geo"] == "DE12"
        assert by_value[20.5]["geo_label"] == "Karlsruhe"

        assert by_value[0.0]["indic_agr"] == "PRD_PP"
        assert by_value[0.0]["geo"] == "DE11"

        assert by_value[-3.5]["am_item"] == "AM011000"
        assert by_value[-3.5]["am_item_label"] == "Wheat and spelt"
        assert by_value[-3.5]["indic_agr"] == "PRD_PP"
        assert by_value[-3.5]["geo"] == "DE12"

    def test_zero_and_negative_values_preserved(self):
        df = mod.melt_json_stat(make_filled_payload(), country="DE")
        values = sorted(df["value"].tolist())
        assert values == [-3.5, 0.0, 10.5, 20.5]

    def test_cast_types_dtypes(self):
        df = mod.cast_types(mod.melt_json_stat(make_filled_payload(), country="DE"))
        assert df["value"].dtype == "float64"
        assert str(df["time"].dtype) == "Int64"
        assert df["time"].tolist() == [2021, 2021, 2021, 2021]
        # string codes are not coerced into numbers
        assert not pd.api.types.is_numeric_dtype(df["am_item"])
        assert set(df["am_item"]) == {"AM010000", "AM011000"}

    def test_country_code_stamped(self):
        df = mod.melt_json_stat(make_filled_payload(), country="DE")
        assert set(df["country_code"]) == {"DE"}


# --------------------------------------------------------------------------
# 2. Valid empty responses
# --------------------------------------------------------------------------

@pytest.mark.parametrize("make_payload", [
    make_empty_year_payload,
    make_sparse_no_value_payload,
    make_status_only_payload,
], ids=["time_size_zero", "sparse_no_value", "status_only"])
def test_valid_empty_response_is_unchanged_not_failed(tmp_path, monkeypatch, caplog, make_payload):
    raw_path = write_raw_file(tmp_path, monkeypatch, make_payload())

    cast_types_calls = []
    monkeypatch.setattr(mod, "cast_types", lambda df: cast_types_calls.append(df) or df)

    with caplog.at_level(logging.INFO):
        result = mod.run(storage_mode="local", countries=["PL"])

    assert result.status != "FAILED"
    assert result.failed_paths == []
    assert result.written_paths == []
    assert result.unchanged_paths == [raw_path]
    assert cast_types_calls == []  # cast_types() must never be reached for NO_DATA
    assert not (tmp_path / mod.NORMALIZED_BASE_DIR).exists()
    assert "No Eurostat observations for requested partition" in caplog.text
    assert "country=PL" in caplog.text


# --------------------------------------------------------------------------
# 3. Observation value types
# --------------------------------------------------------------------------

def _single_cell_payload(value):
    """All-size-1 cube (a single observation at flat index 0) — makes the
    decode trivial so the test is purely about value-type handling."""
    return {
        "class": "dataset",
        "id": ["freq", "am_item", "indic_agr", "unit", "geo", "time"],
        "size": [1, 1, 1, 1, 1, 1],
        "dimension": {
            "freq": {"category": {"index": {"A": 0}, "label": {}}},
            "am_item": {"category": {"index": {"AM010000": 0}, "label": {}}},
            "indic_agr": {"category": {"index": {"PRD_BP": 0}, "label": {}}},
            "unit": {"category": {"index": {"MIO_EUR": 0}, "label": {}}},
            "geo": {"category": {"index": {"DE11": 0}, "label": {}}},
            "time": {"category": {"index": {"2021": 0}, "label": {}}},
        },
        "value": {"0": value},
    }


@pytest.mark.parametrize("raw_value,expected", [
    (0, 0.0),
    (0.0, 0.0),
    (5, 5.0),
    (5.5, 5.5),
    (-12.5, -12.5),
    (None, "skipped"),
    (True, "raises"),
    (False, "raises"),
    ("273.94", "raises"),
    ("not-a-number", "raises"),
])
def test_observation_value_types(raw_value, expected):
    payload = _single_cell_payload(raw_value)

    if expected == "raises":
        with pytest.raises(ValueError):
            mod.melt_json_stat(payload, country="DE")
        return

    df = mod.melt_json_stat(payload, country="DE")

    if expected == "skipped":
        assert df.empty
        return

    assert len(df) == 1
    assert df.iloc[0]["value"] == expected
    assert isinstance(df.iloc[0]["value"], float)


def test_boolean_value_end_to_end_fails_the_file(tmp_path, monkeypatch):
    raw_path = write_raw_file(tmp_path, monkeypatch, _single_cell_payload(True))
    result = mod.run(storage_mode="local", countries=["PL"])
    assert result.status == "FAILED"
    assert raw_path in result.failed_paths
    assert result.written_paths == []


# --------------------------------------------------------------------------
# 4. Corrupted structure
# --------------------------------------------------------------------------

def _missing_id(payload):
    del payload["id"]
    return payload


def _missing_dimension(payload):
    del payload["dimension"]
    return payload


def _id_size_length_mismatch(payload):
    payload["size"] = payload["size"][:-1]
    return payload


def _category_position_out_of_bounds(payload):
    payload["dimension"]["geo"]["category"]["index"]["DE12"] = 99
    return payload


def _value_flat_index_out_of_bounds(payload):
    payload["value"]["9999"] = 1.0  # cube only has 8 cells (indices 0-7)
    return payload


def _size_is_boolean(payload):
    payload["size"][1] = True
    return payload


@pytest.mark.parametrize("corrupt", [
    _missing_id,
    _missing_dimension,
    _id_size_length_mismatch,
    _category_position_out_of_bounds,
    _value_flat_index_out_of_bounds,
    _size_is_boolean,
], ids=[
    "missing_id",
    "missing_dimension",
    "id_size_length_mismatch",
    "category_position_out_of_bounds",
    "value_flat_index_out_of_bounds",
    "size_is_boolean",
])
def test_corrupted_structure_fails_the_file(tmp_path, monkeypatch, corrupt):
    payload = corrupt(make_filled_payload())
    raw_path = write_raw_file(tmp_path, monkeypatch, payload)

    result = mod.run(storage_mode="local", countries=["PL"])

    assert result.status == "FAILED"
    assert raw_path in result.failed_paths
    assert result.written_paths == []
    assert not (tmp_path / mod.NORMALIZED_BASE_DIR).exists()


@pytest.mark.parametrize("corrupt", [
    _missing_id,
    _missing_dimension,
    _id_size_length_mismatch,
    _category_position_out_of_bounds,
    _value_flat_index_out_of_bounds,
    _size_is_boolean,
])
def test_validate_json_stat_structure_raises_directly(corrupt):
    payload = corrupt(make_filled_payload())
    with pytest.raises(ValueError):
        mod.validate_json_stat_structure(payload)
