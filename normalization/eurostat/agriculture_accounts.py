"""
Eurostat regional agricultural accounts normalization.

Reads the raw JSON-stat 2.0 cube saved by
ingestion.eurostat.agriculture_accounts — one file per country and year,
data/raw/eurostat/regional_agricultural_accounts/<country>/<year>/aact_eaa01_r.json
— and melts it into a flat, long-format table: one row per non-null
observation, with each dimension's code *and* human-readable label as
separate columns (freq, am_item, indic_agr, unit, geo, time), plus the
observed value cast to float.

This is a mechanical reshape (decoding what JSON-stat already embeds),
not a business transformation: dimension codes are kept as Eurostat
returns them (e.g. geo="DE11", am_item="AM180000") — nothing is renamed
into domain concepts (no NUTS-level splitting, no am_item/indic_agr
filtering or pivoting, no joins). That's a later, more opinionated step
once the actual columns are reviewed.

JSON-stat decoding: `value` is a flat array (or, for sparse cubes, a
{flat_index: value} object) over the dimensions listed in `id`, in
row-major order — the last dimension in `id` varies fastest. Each flat
index is decoded back into one position per dimension via
`(flat_index // stride) % size`, where a dimension's stride is the
product of the sizes of every dimension listed after it. A dimension's
`category.index` can itself be an array (position = array index) or an
object mapping {code: position} — for the object form, codes are
resolved by sorting on the position *value*, never by assuming the
object's own key order matches it (JSON-stat's spec does not guarantee
that — see docs/pipelines/eurostat_agriculture_accounts.md).

A structurally valid JSON-stat response can still carry zero
observations — confirmed live (PL, 2025): the requested year isn't
published yet, so `value` is `{}` and the `time` dimension's own `size`
is `0`, making the cube's total size `0` too. This is a real, valid
"no data for this partition yet" result, not an error — see
`validate_json_stat_structure()`/`is_valid_empty_result()` below and
run()'s handling of `normalize_file()` returning `None`.

`country_code` (ISO2, from the raw file's own directory) is stamped
explicitly — a different, more directly usable code space than `geo`
(NUTS2, e.g. "DE11"), same convention as every other source in this
project that adds an explicit country_code column.

Confirmed against a real downloaded file (2026-08-20, DE/2021): the cube
has 90 am_item codes and 4 indic_agr codes (not just the one narrow
series ingestion's discovery queries use) — 4976 of the 90×4×38=13680
possible cells have an actual value; a further 4636 have no value but do
have a `status` flag explaining why (Eurostat's real data uses "m",
meaning "data cannot exist for this combination" per SDMX's
CL_OBS_STATUS); the remaining ~4068 cells are simply absent with no
flag. `status`'s keys never overlap with `value`'s keys, so it can't be
attached as a column on the observations table — see
summarize_status_flags(), logged per file rather than silently dropped.

`countries` must be passed explicitly — run() never defaults to scanning
and processing every country on disk (see docs/pipelines/countries.md).
Each entry can be a country code, a finer "country/year" partition
prefix, or an exact raw file path (common.storage.resolve_paths).

Reads/writes go through common.storage, so storage_mode="local" (default)
and storage_mode="cloud" (S3) run the same logic.

    from normalization.eurostat.agriculture_accounts import run, discover_countries
    run(countries=["DE", "PL"])
    run(countries=discover_countries("local"))
    run(countries=["DE"], storage_mode="cloud")
"""
import json
import logging
import re
import sys
from io import BytesIO
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.manifest import StageResult
from common.storage import list_files, read_bytes, resolve_paths, write_bytes

logger = logging.getLogger(__name__)


def category_codes(payload: dict, dimension: str) -> list[str]:
    """
    Codes for one dimension, in *position* order (position 0 first).
    JSON-stat 2.0's category.index is either an array (position = array
    index) or an object mapping {code: position} — for the object form,
    dict key order is not guaranteed by the spec to match position order,
    so codes are always resolved by sorting on the position value, never
    by assuming iteration order. Duplicated from
    ingestion.eurostat.agriculture_accounts (same small, source-specific,
    pure decoding function needed at both stages) rather than importing
    across the ingestion/normalization boundary.
    """
    index = payload["dimension"][dimension]["category"]["index"]
    if isinstance(index, list):
        return index
    if isinstance(index, dict):
        return [code for code, _position in sorted(index.items(), key=lambda item: item[1])]
    raise RuntimeError(f"Unexpected JSON-stat category index shape for {dimension!r}: {type(index)}")


def _is_plain_int(value) -> bool:
    """True for a genuine JSON integer — excludes bool, which is a Python
    `int` subclass but never a valid size/position/flat-index here."""
    return isinstance(value, int) and not isinstance(value, bool)


def validate_json_stat_structure(payload: dict) -> None:
    """
    Structural/internal-consistency checks beyond common.validation's
    is_valid_json_stat (which only confirms the top-level id/size/
    dimension/value keys exist — ingestion's own gate before a raw file
    ever reaches storage). Run again here because normalization reads
    whatever is already on disk, and a corrupt/truncated/hand-edited file
    should fail loudly and land in failed_paths (see run()), never crash
    melt_json_stat()/cast_types() with a raw KeyError/IndexError deep
    inside a loop. Raises ValueError with a specific reason on any
    inconsistency; returns None (does nothing) when the file is
    structurally sound — that includes a structurally valid file with
    zero observations, see is_valid_empty_result() for that case.
    """
    if "class" in payload and payload["class"] != "dataset":
        raise ValueError(f"Unexpected JSON-stat 'class': {payload['class']!r} (expected 'dataset')")

    dimension_ids = payload.get("id")
    sizes = payload.get("size")
    if not isinstance(dimension_ids, list):
        raise ValueError(f"JSON-stat 'id' is missing or not a list: {dimension_ids!r}")
    if not isinstance(sizes, list):
        raise ValueError(f"JSON-stat 'size' is missing or not a list: {sizes!r}")
    if len(dimension_ids) != len(sizes):
        raise ValueError(
            f"JSON-stat 'id'/'size' length mismatch: len(id)={len(dimension_ids)} len(size)={len(sizes)}"
        )
    for dim, size in zip(dimension_ids, sizes):
        if not _is_plain_int(size) or size < 0:
            raise ValueError(f"JSON-stat 'size' for dimension {dim!r} is not a non-negative int: {size!r}")

    dimensions = payload.get("dimension")
    if not isinstance(dimensions, dict):
        raise ValueError(f"JSON-stat 'dimension' is missing or not an object: {dimensions!r}")
    for dim, size in zip(dimension_ids, sizes):
        if dim not in dimensions:
            raise ValueError(f"JSON-stat 'dimension' has no entry for {dim!r} (listed in 'id')")
        index = (dimensions[dim].get("category") or {}).get("index")
        if not index:
            continue  # a size-0 dimension legitimately has an empty/absent index (see is_valid_empty_result)
        positions = index if isinstance(index, list) else index.values()
        for position in positions:
            if not _is_plain_int(position) or not (0 <= position < size):
                raise ValueError(
                    f"JSON-stat category position out of bounds for dimension {dim!r}: "
                    f"position={position!r} size={size!r}"
                )

    value = payload.get("value")
    if value is not None and not isinstance(value, (dict, list)):
        raise ValueError(f"JSON-stat 'value' must be an object or array, got {type(value).__name__}")

    total_cells = 1
    for size in sizes:
        total_cells *= size
    flat_indices = value.keys() if isinstance(value, dict) else range(len(value)) if isinstance(value, list) else []
    for flat_index in flat_indices:
        try:
            idx = int(flat_index)
        except (TypeError, ValueError):
            raise ValueError(f"JSON-stat 'value' has a non-integer flat index: {flat_index!r}") from None
        if not (0 <= idx < total_cells):
            raise ValueError(
                f"JSON-stat 'value' flat index out of bounds: {flat_index!r} (cube size={total_cells})"
            )


def is_valid_empty_result(payload: dict) -> bool:
    """
    True for a structurally valid JSON-stat response that legitimately
    carries zero observations (Eurostat's own NO_DATA — e.g. the
    requested year isn't published yet), per any of:
      - 'value' is missing/empty (covers both a fully sparse cube with
        only status flags, and a genuinely empty one);
      - any dimension's 'size' is 0 (makes the cube's total size 0 too);
      - the 'time' dimension's own category.index is empty.
    Call only after validate_json_stat_structure() has passed — this
    doesn't re-check structural soundness, only emptiness.
    """
    if not payload.get("value"):
        return True
    if any(size == 0 for size in payload["size"]):
        return True
    time_index = (((payload.get("dimension") or {}).get("time") or {}).get("category") or {}).get("index")
    if time_index is not None and len(time_index) == 0:
        return True
    return False


RAW_BASE_DIR = "data/raw/eurostat/regional_agricultural_accounts"
NORMALIZED_BASE_DIR = "data/normalized/eurostat/regional_agricultural_accounts"
RAW_FILENAME = "aact_eaa01_r.json"


def discover_countries(storage_mode: str) -> list[str]:
    """Country codes come from the raw layer's own <country>/ subdirectories."""
    raw_files = list_files(RAW_BASE_DIR, storage_mode, suffix=RAW_FILENAME)
    return sorted({path[len(RAW_BASE_DIR):].lstrip("/").split("/")[0] for path in raw_files})


def compute_strides(sizes: list[int]) -> list[int]:
    """stride[i] = product of sizes of every dimension after position i —
    how much the flat index advances when only dimension i's own position
    advances by one, holding every other dimension fixed."""
    strides = []
    for i in range(len(sizes)):
        stride = 1
        for size in sizes[i + 1:]:
            stride *= size
        strides.append(stride)
    return strides


def decode_flat_index(flat_index: int, sizes: list[int], strides: list[int]) -> list[int]:
    """Row-major flat index -> one category position per dimension, same
    order as `sizes`/`strides` (see module docstring)."""
    return [(flat_index // strides[i]) % sizes[i] for i in range(len(sizes))]


def summarize_status_flags(payload: dict) -> dict:
    """
    JSON-stat's optional `status` field flags *why* a cell has no value —
    e.g. Eurostat's real data confirmed the flag "m" ("data cannot exist
    for this combination", per SDMX's CL_OBS_STATUS). Confirmed live: its
    keys (flat indices) never overlap with `value`'s keys — a cell either
    has a number or a status flag explaining its absence, never both — so
    there's no per-row column to attach this to in melt_json_stat's output
    (that table is "one row per actual observation"). Logged here instead
    so the information isn't silently lost; not merged into the main
    table pending a transformation-stage decision on whether/how it's
    useful downstream.
    """
    total_cells = 1
    for size in payload["size"]:
        total_cells *= size
    value_count = len(payload.get("value") or {})
    status = payload.get("status") or {}
    status_count = len(status)
    unexplained = total_cells - value_count - status_count
    return {
        "total_cells": total_cells,
        "with_value": value_count,
        "with_status_flag": status_count,
        "status_flag_codes": sorted(set(status.values())) if isinstance(status, dict) else [],
        "unexplained_empty": unexplained,
    }


def coerce_observation_value(value, flat_index) -> float:
    """
    A JSON-stat observation is a genuine JSON number (int or float) or
    null (null is filtered out by the caller before this is reached — a
    missing observation, not a zero one). Anything else fails loudly
    instead of silently becoming a number:
      - bool is rejected even though Python's `bool` is an `int`
        subclass (float(True) == 1.0 would otherwise silently turn a
        stray boolean into an observation);
      - a numeric string (e.g. "273.94") is rejected too — this
        project's normalized contract is float64, and nothing in real
        Eurostat responses observed so far puts value under quotes;
        accepting stringified numbers would be new, undocumented
        behavior, not a fix for the reported bug;
      - anything else (other strings, dict, list) is rejected.
    0/0.0 and negative numbers are real observations and pass through
    unchanged — only the *type* is checked here, never the sign or
    truthiness.
    """
    if isinstance(value, bool):
        raise ValueError(f"JSON-stat 'value' at flat index {flat_index!r} is a boolean, not a number: {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    raise ValueError(
        f"JSON-stat 'value' at flat index {flat_index!r} has an unexpected type "
        f"{type(value).__name__!r}: {value!r}"
    )


def melt_json_stat(payload: dict, country: str) -> pd.DataFrame:
    dimension_ids = payload["id"]
    sizes = payload["size"]
    strides = compute_strides(sizes)

    codes_by_dimension = {dim: category_codes(payload, dim) for dim in dimension_ids}
    labels_by_dimension = {
        dim: (payload["dimension"][dim]["category"].get("label") or {})
        for dim in dimension_ids
    }

    raw_values = payload.get("value") or {}
    items = raw_values.items() if isinstance(raw_values, dict) else enumerate(raw_values)

    rows = []
    for flat_index, value in items:
        if value is None:
            continue  # a missing observation — see coerce_observation_value for why 0/0.0 is NOT skipped here
        positions = decode_flat_index(int(flat_index), sizes, strides)

        row = {"country_code": country}
        for dim, position in zip(dimension_ids, positions):
            code = codes_by_dimension[dim][position]
            row[dim] = code
            row[f"{dim}_label"] = labels_by_dimension[dim].get(code)
        row["value"] = coerce_observation_value(value, flat_index)
        rows.append(row)

    return pd.DataFrame(rows)


# This dataset is annual (freq="A") throughout this pipeline's scope — a
# 'time' category code is expected to always be a plain 4-digit year
# string ("2021"). See cast_types(): anything else fails loudly rather
# than silently becoming <NA> via pd.to_numeric(errors="coerce").
YEAR_CODE_PATTERN = re.compile(r"^\d{4}$")


def cast_types(df: pd.DataFrame) -> pd.DataFrame:
    if "time" in df.columns:
        bad = sorted(df.loc[~df["time"].astype(str).str.match(YEAR_CODE_PATTERN), "time"].unique())
        if bad:
            raise ValueError(f"Unexpected 'time' category code(s), expected a 4-digit year: {bad!r}")
        df["time"] = df["time"].astype("Int64")
    # value is already a clean Python float from coerce_observation_value()
    # (melt_json_stat) — this only pins the pandas dtype, it doesn't coerce
    # anything, so nothing here can silently turn a bad value into NaN.
    df["value"] = df["value"].astype("float64")
    return df


def normalize_file(raw_path: str, storage_mode: str) -> str | None:
    """
    Returns the written parquet path, or None for a structurally valid
    JSON-stat response that carries no observations (NO_DATA — e.g. the
    requested year isn't published by Eurostat yet). A None return is
    not a failure: run() records it via record_unchanged(), same as any
    other "nothing to write" stage result in this project. A genuinely
    corrupt/inconsistent raw file raises instead (see
    validate_json_stat_structure/coerce_observation_value/cast_types),
    which run()'s try/except turns into a failed_paths entry.
    """
    payload = json.loads(read_bytes(raw_path, storage_mode))
    validate_json_stat_structure(payload)

    relative = raw_path[len(RAW_BASE_DIR):].lstrip("/")
    parts = relative.split("/")
    country = parts[0]
    year = parts[1] if len(parts) > 1 else "unknown"

    status_summary = summarize_status_flags(payload)
    logger.info(
        "Cube cell accounting | raw=%s total_cells=%s with_value=%s with_status_flag=%s "
        "status_flag_codes=%s unexplained_empty=%s",
        raw_path, status_summary["total_cells"], status_summary["with_value"],
        status_summary["with_status_flag"], status_summary["status_flag_codes"],
        status_summary["unexplained_empty"],
    )

    if is_valid_empty_result(payload):
        logger.info("No Eurostat observations for requested partition | country=%s year=%s raw=%s",
                     country, year, raw_path)
        return None

    df = melt_json_stat(payload, country)
    if df.empty:
        logger.info("No Eurostat observations for requested partition | country=%s year=%s raw=%s",
                     country, year, raw_path)
        return None
    df = cast_types(df)

    out_path = f"{NORMALIZED_BASE_DIR}/{relative.removesuffix('.json')}.parquet"
    buffer = BytesIO()
    df.to_parquet(buffer, index=False)
    write_bytes(out_path, buffer.getvalue(), storage_mode)

    logger.info("Normalized file saved | raw=%s -> normalized=%s rows=%s", raw_path, out_path, len(df))
    return out_path


def run(storage_mode: str = "local", countries: list[str] | None = None) -> StageResult:
    if not countries:
        raise ValueError(
            "countries must be provided explicitly — e.g. countries=['DE'], or "
            "countries=discover_countries(storage_mode) to process every country "
            "already ingested. run() does not default to processing everything on disk."
        )

    raw_files = resolve_paths(countries, RAW_BASE_DIR, storage_mode, suffix=".json")
    raw_files = [p for p in raw_files if not p.endswith("state.json")]

    if not raw_files:
        logger.warning("No raw agricultural accounts files found for countries=%s under %s",
                       countries, RAW_BASE_DIR)
        return StageResult().finalize(attempted=0)

    logger.info("Starting Eurostat agricultural accounts normalization | countries=%s files=%s storage_mode=%s",
                countries, len(raw_files), storage_mode)

    result = StageResult()
    for raw_path in raw_files:
        try:
            out_path = normalize_file(raw_path, storage_mode)
            if out_path is None:
                result.record_unchanged(raw_path)
            else:
                result.record_written(out_path)
        except Exception:
            logger.exception("Agricultural accounts normalization failed | raw=%s", raw_path)
            result.record_failed(raw_path)

    logger.info("Eurostat agricultural accounts normalization finished | files=%s written=%s failed=%s",
                len(raw_files), len(result.written_paths), len(result.failed_paths))
    return result.finalize(attempted=len(raw_files))


if __name__ == "__main__":
    run(
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
        countries=["DE", "PL"],  # required — or discover_countries("local") for everything ingested
    )
