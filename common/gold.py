"""
Shared helpers for Gold Layer modules (gold/<source>/*.py) — the final,
analysis-ready layer: one Parquet file per *precursor partition*, only
the columns that matter for analysis kept and named.

Gold mirrors the same partitioning its precursor stage (normalization
or transformation) already uses, one output file per input file — not
one giant combined file for the whole source. This is what makes a
Gold build incremental: a run only reads+rewrites the specific
partition(s) its precursor actually touched this run (via
--input-manifest, exactly like normalization/transformation already
do), leaving every other partition's Gold file untouched. There's
deliberately no "combine many precursor files into one Gold table"
step here — each precursor partition file already fully represents its
own partition, so building+writing one Gold partition is a single-file
operation (build_gold_partition/gold_partition_path/write_gold_table),
plus, since dtypes must never depend on what pandas/pyarrow happen to
infer, "cast these columns to these exact dtypes" and "drop rows
missing any of these required columns" (enforce_dtypes/
drop_missing_required). Which precursor stage to read, which base
directory, and which columns/renames/dtypes/required-fields stay in
each source's own gold/<source>/*.py module, matching this project's
"source-specific logic stays in the source module" rule.

    from common.gold import build_gold_partition, gold_partition_path, enforce_dtypes, drop_missing_required, write_gold_table
"""
import logging
from io import BytesIO

import pandas as pd

from common.storage import read_bytes, write_bytes

logger = logging.getLogger(__name__)


def build_gold_partition(
    path: str,
    storage_mode: str,
    columns: list[str],
    rename: dict[str, str] | None = None,
) -> pd.DataFrame:
    """
    Reads one precursor Parquet file, keeps only `columns` in that exact
    order (a file missing one of them is logged and contributes NaN for
    it rather than failing the whole build — schemas upstream can
    evolve), applies `rename` last so `columns` always names *source*
    columns, and drops exact duplicate rows within this one file
    (deterministic — pandas keeps the first occurrence).
    """
    df = pd.read_parquet(BytesIO(read_bytes(path, storage_mode)))
    missing = [c for c in columns if c not in df.columns]
    if missing:
        logger.warning("Gold source file missing expected column(s) | path=%s missing=%s", path, missing)
    present = [c for c in columns if c in df.columns]
    df = df[present].reindex(columns=columns)
    if rename:
        df = df.rename(columns=rename)

    before = len(df)
    df = df.drop_duplicates().reset_index(drop=True)
    if len(df) != before:
        logger.info("Gold partition deduplicated | path=%s %s -> %s rows", path, before, len(df))

    return df


def gold_partition_path(precursor_path: str, precursor_base_dir: str, gold_base_dir: str,
                         filename_prefix: str) -> str:
    """
    Mirrors a precursor file's own partition structure into a flat Gold
    file name — e.g. `<precursor_base_dir>/DE/2021/PM10/measurements.parquet`
    (EEA: country/year/pollutant) -> `<gold_base_dir>/measurements_DE_2021_PM10.parquet`,
    `<precursor_base_dir>/DE/notices.parquet` (TED: country only) ->
    `<gold_base_dir>/notices_DE.parquet`. Deliberately flat (no
    subdirectories) under gold_base_dir — Athena/Glue's table `location`
    is that directory itself, and a flat layout avoids any doubt about
    whether S3-backed recursive prefix listing is in effect.
    """
    relative = precursor_path[len(precursor_base_dir):].lstrip("/")
    partition_segments = relative.split("/")[:-1]  # drop the file name itself
    suffix = "_".join(partition_segments)
    return f"{gold_base_dir}/{filename_prefix}_{suffix}.parquet"


def enforce_dtypes(df: pd.DataFrame, dtypes: dict[str, str]) -> pd.DataFrame:
    """
    Casts each named column to its declared dtype, deterministically —
    a precursor file's own dtype (whatever normalization/transformation
    happened to produce, or an older code version wrote) is never
    trusted as-is; Gold's own schema is always the one actually written,
    every time a partition is rewritten. Call this right after
    build_gold_partition(), and before drop_missing_required()/
    write_gold_table() — required-field detection depends on types
    already being consistent.

    Supported dtype kinds (the values in `dtypes`):
      - "string": pandas nullable StringDtype. Use this for every code/
        label/identifier column, even ones that happen to look numeric —
        never cast an identifier to a numeric dtype: leading zeros and
        non-digit vocabulary codes must survive (e.g. EEA's
        pollutant_code is an EEA vocabulary code, not an arithmetic
        value).
      - "Int64": nullable integer, for genuinely numeric whole-number
        fields (e.g. a reference year).
      - "float64": plain float.
      - "datetime64[ns]": full timestamp.
      - "date": calendar date only, no time component (Python
        `datetime.date`, matching how e.g. TED's own dates are already
        parsed upstream — see normalization/ted/notices.py's
        parse_ted_date).
    """
    df = df.copy()
    for column, kind in dtypes.items():
        if column not in df.columns:
            continue
        if kind == "string":
            df[column] = df[column].astype("string")
        elif kind == "Int64":
            df[column] = pd.to_numeric(df[column], errors="coerce").astype("Int64")
        elif kind == "float64":
            df[column] = pd.to_numeric(df[column], errors="coerce").astype("float64")
        elif kind == "datetime64[ns]":
            # pandas >= 2 infers a datetime64 *resolution* from the input
            # (e.g. "us" for a microsecond-precision string) rather than
            # always "ns" — the explicit .astype() below is what actually
            # makes this deterministic, not pd.to_datetime() alone.
            df[column] = pd.to_datetime(df[column], errors="coerce").astype("datetime64[ns]")
        elif kind == "date":
            df[column] = pd.to_datetime(df[column], errors="coerce").dt.date
        else:
            raise ValueError(f"Unknown Gold dtype kind {kind!r} for column {column!r}")
    return df


def drop_missing_required(df: pd.DataFrame, required_columns: list[str]) -> pd.DataFrame:
    """
    Drops rows missing any of `required_columns` — call this after
    enforce_dtypes(), not before: reliably telling "missing" from
    "present" depends on the column already having a consistent dtype.
    An empty or whitespace-only string in a required string column is
    normalized to a real missing value first, so a blank string isn't
    silently treated as "present" (a `dropna()` alone would miss it —
    it only catches actual NA/NaT/NaN).

    Each source decides its own `required_columns` — e.g. TED
    deliberately excludes contract_total_value/contract_currency_code:
    a notice missing its value still counts for notice-count metrics,
    just not for value-aggregating ones (which must filter for those
    two explicitly in the query itself).
    """
    df = df.copy()
    present = [c for c in required_columns if c in df.columns]
    for column in present:
        if df[column].dtype == "string" or df[column].dtype == object:
            df[column] = df[column].replace(r"^\s*$", pd.NA, regex=True)

    before = len(df)
    df = df.dropna(subset=present).reset_index(drop=True)
    dropped = before - len(df)
    if dropped:
        logger.info("Gold table dropped rows missing required field(s) | dropped=%s required=%s",
                     dropped, present)
    return df


def write_gold_table(df: pd.DataFrame, out_path: str, storage_mode: str) -> None:
    buffer = BytesIO()
    df.to_parquet(buffer, index=False)
    write_bytes(out_path, buffer.getvalue(), storage_mode)
    logger.info("Gold table written | path=%s rows=%s", out_path, len(df))
