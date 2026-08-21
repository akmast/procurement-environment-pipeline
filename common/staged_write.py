"""
Staging → validate → hash-compare → promote — the shared write path used
by every ingestion pipeline that redownloads a whole snapshot file (EEA
stations, EEA measurements, TED codelists, Eurostat agricultural
accounts).

Downloaded content is never written straight to its final location. It
lands in a staging path first, gets read back and validated there
(confirming it's actually readable in its expected format, not just that
bytes arrived), and is only promoted to final_path if validation passes
*and* its content hash differs from what's already recorded — validation
and the hash check are two separate gates, neither is sufficient alone.
Staging is always cleaned up afterwards, whether or not the write
happened. Identical flow for storage_mode="local" and "cloud" — every
step routes through common.storage, so there's nothing mode-specific
here.

Not used by ingestion.ted.notices — that source is an append-only stream
deduplicated per-record already, not a redownloaded snapshot file (same
reason it's excluded from common.change_tracking, see that module).

    from common.staged_write import stage_validate_and_write, WRITE_RESULT_WRITTEN
    result = stage_validate_and_write(path, content, storage_mode, state, validate=is_valid_json)
    if result == WRITE_RESULT_WRITTEN: ...
"""
import logging
from typing import Callable

from common.change_tracking import has_changed
from common.storage import delete, read_bytes, write_bytes

logger = logging.getLogger(__name__)

STAGING_PREFIX = "staging"

# stage_validate_and_write()'s three possible outcomes. A plain bool used
# to be enough for "did this get (re)written", but callers building a
# common.manifest.StageResult need to tell "unchanged" (a normal skip)
# apart from "invalid" (a real failure — the download was corrupted or
# the source API's shape changed) instead of collapsing both into one
# falsy value.
WRITE_RESULT_WRITTEN = "written"
WRITE_RESULT_UNCHANGED = "unchanged"
WRITE_RESULT_INVALID = "invalid"


def stage_validate_and_write(
    final_path: str,
    content: bytes,
    storage_mode: str,
    state: dict,
    validate: Callable[[bytes], bool],
) -> str:
    """
    Returns one of WRITE_RESULT_WRITTEN / WRITE_RESULT_UNCHANGED /
    WRITE_RESULT_INVALID. state[final_path] is updated only on
    WRITE_RESULT_WRITTEN — the caller persists `state` itself via
    common.change_tracking.save_state once it's done writing everything
    it needs to for the run.
    """
    staging_path = f"{STAGING_PREFIX}/{final_path}"
    write_bytes(staging_path, content, storage_mode)

    try:
        staged_content = read_bytes(staging_path, storage_mode)

        if not validate(staged_content):
            logger.error("Validation failed, not promoting to final storage | path=%s", final_path)
            return WRITE_RESULT_INVALID

        changed, new_hash = has_changed(state, final_path, staged_content)
        if not changed:
            logger.info("Unchanged after validation, skipped | path=%s", final_path)
            return WRITE_RESULT_UNCHANGED

        write_bytes(final_path, staged_content, storage_mode)
        state[final_path] = {"content_hash": new_hash}
        return WRITE_RESULT_WRITTEN
    finally:
        delete(staging_path, storage_mode)
