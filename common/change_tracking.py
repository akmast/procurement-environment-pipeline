"""
Content-hash based change detection, shared across ingestion pipelines
that redownload whole "snapshot" files (EEA stations, EEA measurements,
TED codelists).

The question this answers is specifically "did the content change from
what we already have stored", by hashing the *bytes actually
downloaded* — never file name, timestamp, or S3/filesystem metadata,
none of which say anything about content. A tracked state entry is kept
per file (keyed by its own storage path) so this works the same way in
both storage_mode="local" and "cloud" — the state itself is just another
file, read/written through common.storage.

    from common.change_tracking import compute_hash, load_state, save_state, has_changed

Not used by ingestion.ted.notices — that source is an append-only stream
deduplicated per-record by publication-number already, not a redownloaded
snapshot file, so whole-file hashing doesn't apply there.
"""
import hashlib
import json
import logging

from common.storage import read_bytes, write_bytes, exists

logger = logging.getLogger(__name__)


def compute_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def load_state(state_path: str, storage_mode: str) -> dict:
    if not exists(state_path, storage_mode):
        return {}
    return json.loads(read_bytes(state_path, storage_mode).decode("utf-8"))


def save_state(state_path: str, state: dict, storage_mode: str) -> None:
    write_bytes(
        state_path,
        json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8"),
        storage_mode,
    )


def has_changed(state: dict, key: str, content: bytes) -> tuple[bool, str]:
    """
    Returns (changed, new_hash). `changed` is True when `key` is new to
    the state or its stored hash differs from the freshly computed one.
    Caller decides what to do — this only answers the yes/no question.
    """
    new_hash = compute_hash(content)
    old_hash = state.get(key, {}).get("content_hash")
    return new_hash != old_hash, new_hash
