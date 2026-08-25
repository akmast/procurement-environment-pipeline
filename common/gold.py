"""
Shared helpers for Gold Layer modules (gold/<source>/*.py) — the final,
analysis-ready layer: one Parquet file per source, every country/year
combined, only the columns that matter for analysis kept and named.

The things genuinely common across eurostat/eea/ted's own Gold builders:
"read every partition file found, keep+order+rename these columns, drop
exact duplicate rows, write one combined file" (build_gold_table/
write_gold_table), plus, since a build concatenates many partition
files that can individually drift in dtype (a stale file written by an
older code version, a partition that's all-null in one column, ...),
"cast these columns to these exact dtypes" and "drop rows missing any
of these required columns" (enforce_dtypes/drop_missing_required) —
never left to whatever pandas/pyarrow happens to infer from the
concatenation. Which precursor stage to read, which base directory, and
which columns/renames/dtypes/required-fields stay in each source's own
gold/<source>/*.py module, matching this project's "source-specific
logic stays in the source module" rule.

    from common.gold import build_gold_table, enforce_dtypes, drop_missing_required, write_gold_table
"""
import logging
from io import BytesIO

import pandas as pd

from common.storage import read_bytes, write_bytes

logger = logging.getLogger(__name__)


def build_gold_table(
    paths: list[str],
    storage_mode: str,
    columns: list[str],
    rename: dict[str, str] | None = None,
) -> pd.DataFrame:
    """
    Reads every Parquet file in `paths`, concatenates them, keeps only
    `columns` in that exact order (a file missing one of them is logged
    and contributes NaN for it rather than failing the whole build —
    schemas upstream can evolve), applies `rename` last so `columns`
    always names *source* columns, and drops exact duplicate rows
    (deterministic: pandas keeps the first occurrence, and `paths` comes
    from common.storage.resolve_paths/list_files, which both return
    sorted paths).

    This always rebuilds the full table from `paths` — Gold Layer has no
    partitioning of its own to incrementally merge into.
    """
    if not paths:
        empty_columns = [rename.get(c, c) if rename else c for c in columns]
        return pd.DataFrame(columns=empty_columns)

    frames = []
    for path in paths:
        df = pd.read_parquet(BytesIO(read_bytes(path, storage_mode)))
        missing = [c for c in columns if c not in df.columns]
        if missing:
            logger.warning("Gold source file missing expected column(s) | path=%s missing=%s", path, missing)
        present = [c for c in columns if c in df.columns]
        frames.append(df[present])

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.reindex(columns=columns)
    if rename:
        combined = combined.rename(columns=rename)

    before = len(combined)
    combined = combined.drop_duplicates().reset_index(drop=True)
    if len(combined) != before:
        logger.info("Gold table deduplicated | %s -> %s rows", before, len(combined))

    return combined


def enforce_dtypes(df: pd.DataFrame, dtypes: dict[str, str]) -> pd.DataFrame:
    """
    Casts each named column to its declared dtype, deterministically —
    a build_gold_table() concatenation can otherwise end up with a
    column's effective dtype depending on which partition files happened
    to be involved (e.g. one stale file with a column stored as plain
    object/string is enough to widen an otherwise-numeric column for the
    whole combined table). Call this right after build_gold_table(), and
    before drop_missing_required()/write_gold_table() — required-field
    detection depends on types already being consistent.

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
