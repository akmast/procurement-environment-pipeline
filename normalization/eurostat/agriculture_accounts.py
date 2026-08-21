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
that, and Eurostat's ordering was not independently confirmed against a
live response in this sandbox — see
docs/pipelines/eurostat_agriculture_accounts.md).

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


def melt_json_stat(payload: dict, country: str) -> pd.DataFrame:
    dimension_ids = payload["id"]
    sizes = payload["size"]
    strides = compute_strides(sizes)

    codes_by_dimension = {dim: category_codes(payload, dim) for dim in dimension_ids}
    labels_by_dimension = {
        dim: (payload["dimension"][dim]["category"].get("label") or {})
        for dim in dimension_ids
    }

    raw_values = payload.get("value", {})
    items = raw_values.items() if isinstance(raw_values, dict) else enumerate(raw_values)

    rows = []
    for flat_index, value in items:
        if value is None:
            continue
        positions = decode_flat_index(int(flat_index), sizes, strides)

        row = {"country_code": country}
        for dim, position in zip(dimension_ids, positions):
            code = codes_by_dimension[dim][position]
            row[dim] = code
            row[f"{dim}_label"] = labels_by_dimension[dim].get(code)
        row["value"] = float(value)
        rows.append(row)

    return pd.DataFrame(rows)


def cast_types(df: pd.DataFrame) -> pd.DataFrame:
    if "time" in df.columns:
        df["time"] = pd.to_numeric(df["time"], errors="coerce").astype("Int64")
    df["value"] = pd.to_numeric(df["value"], errors="coerce").astype(float)
    return df


def normalize_file(raw_path: str, storage_mode: str) -> str:
    payload = json.loads(read_bytes(raw_path, storage_mode))

    relative = raw_path[len(RAW_BASE_DIR):].lstrip("/")
    country = relative.split("/")[0]

    df = melt_json_stat(payload, country)
    df = cast_types(df)

    status_summary = summarize_status_flags(payload)
    logger.info(
        "Cube cell accounting | raw=%s total_cells=%s with_value=%s with_status_flag=%s "
        "status_flag_codes=%s unexplained_empty=%s",
        raw_path, status_summary["total_cells"], status_summary["with_value"],
        status_summary["with_status_flag"], status_summary["status_flag_codes"],
        status_summary["unexplained_empty"],
    )

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
