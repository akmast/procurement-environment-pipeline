"""
Staging → validate → hash-compare → promote — the shared write path used
by every ingestion pipeline that redownloads a whole snapshot file (EEA
stations, EEA measurements, TED codelists).

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

    from common.staged_write import stage_validate_and_write
"""
import logging
from typing import Callable

from common.change_tracking import has_changed
from common.storage import delete, read_bytes, write_bytes

logger = logging.getLogger(__name__)

STAGING_PREFIX = "staging"


def stage_validate_and_write(
    final_path: str,
    content: bytes,
    storage_mode: str,
    state: dict,
    validate: Callable[[bytes], bool],
) -> bool:
    """
    Returns True if final_path was (re)written, False if validation
    failed or the content was unchanged (state[final_path] is updated
    in that case, but not written to disk/S3 — the caller persists
    `state` itself via common.change_tracking.save_state once it's done
    writing everything it needs to for the run).
    """
    staging_path = f"{STAGING_PREFIX}/{final_path}"
    write_bytes(staging_path, content, storage_mode)

    try:
        staged_content = read_bytes(staging_path, storage_mode)

        if not validate(staged_content):
            logger.error("Validation failed, not promoting to final storage | path=%s", final_path)
            return False

        changed, new_hash = has_changed(state, final_path, staged_content)
        if not changed:
            logger.info("Unchanged after validation, skipped | path=%s", final_path)
            return False

        write_bytes(final_path, staged_content, storage_mode)
        state[final_path] = {"content_hash": new_hash}
        return True
    finally:
        delete(staging_path, storage_mode)
