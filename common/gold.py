"""
Shared helpers for Gold Layer modules (gold/<source>/*.py) — the final,
analysis-ready layer: one Parquet file per source, every country/year
combined, only the columns that matter for analysis kept and named.

The only thing genuinely common across eurostat/eea/ted's own Gold
builders is "read every partition file found, keep+order+rename these
columns, drop exact duplicate rows, write one combined file" — which
precursor stage to read (normalization or transformation — eurostat has
no transformation stage), which base directory, and which columns/
renames stay in each source's own gold/<source>/*.py module, matching
this project's "source-specific logic stays in the source module" rule.

    from common.gold import build_gold_table, write_gold_table
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


def write_gold_table(df: pd.DataFrame, out_path: str, storage_mode: str) -> None:
    buffer = BytesIO()
    df.to_parquet(buffer, index=False)
    write_bytes(out_path, buffer.getvalue(), storage_mode)
    logger.info("Gold table written | path=%s rows=%s", out_path, len(df))
