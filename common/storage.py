"""
Uniform local/S3 storage layer.

Every ingestion/normalization/transformation module reads and writes
through the functions here instead of touching `pathlib`/`open()` or
`boto3` directly — that's what keeps business logic identical between
`storage_mode="local"` and `storage_mode="cloud"`. A module never
branches on storage_mode itself; it just passes a project-relative path
(e.g. "data/raw/eea/stations/stations_raw.json") and the mode through.

    from common.storage import read_bytes, write_bytes, exists, list_files, head_metadata

Cloud mode needs the PIPELINE_S3_BUCKET environment variable set and AWS
credentials available (however boto3 normally picks them up — env vars,
~/.aws/credentials, an instance role, etc.) — this module doesn't handle
auth itself, boto3 does.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VALID_MODES = ("local", "cloud")


def _validate_mode(storage_mode: str) -> None:
    if storage_mode not in VALID_MODES:
        raise ValueError(f"Unknown storage_mode {storage_mode!r} — expected 'local' or 'cloud'")


def _bucket_name() -> str:
    import os
    bucket = os.environ.get("PIPELINE_S3_BUCKET")
    if not bucket:
        raise RuntimeError(
            "storage_mode='cloud' requires the PIPELINE_S3_BUCKET environment "
            "variable to be set to the target S3 bucket name."
        )
    return bucket


def _s3_client():
    import boto3
    return boto3.client("s3")


def write_bytes(relative_path: str, content: bytes, storage_mode: str) -> None:
    _validate_mode(storage_mode)
    if storage_mode == "local":
        local_path = PROJECT_ROOT / relative_path
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(content)
    else:
        _s3_client().put_object(Bucket=_bucket_name(), Key=relative_path, Body=content)
    logger.debug("Wrote %s bytes | mode=%s path=%s", len(content), storage_mode, relative_path)


def read_bytes(relative_path: str, storage_mode: str) -> bytes:
    _validate_mode(storage_mode)
    if storage_mode == "local":
        return (PROJECT_ROOT / relative_path).read_bytes()
    resp = _s3_client().get_object(Bucket=_bucket_name(), Key=relative_path)
    return resp["Body"].read()


def write_text(relative_path: str, text: str, storage_mode: str) -> None:
    write_bytes(relative_path, text.encode("utf-8"), storage_mode)


def read_text(relative_path: str, storage_mode: str) -> str:
    return read_bytes(relative_path, storage_mode).decode("utf-8")


def append_text(relative_path: str, text: str, storage_mode: str) -> None:
    """
    Append text to a file. Local mode uses a real filesystem append; S3 has
    no append operation, so cloud mode reads the existing object (if any)
    and rewrites it with the new text tacked on. Fine for the moderate,
    append-a-few-lines-at-a-time sizes this project deals with — not meant
    for very large or very frequently appended files.
    """
    _validate_mode(storage_mode)
    if storage_mode == "local":
        local_path = PROJECT_ROOT / relative_path
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with open(local_path, "a", encoding="utf-8") as f:
            f.write(text)
    else:
        existing = read_text(relative_path, storage_mode) if exists(relative_path, storage_mode) else ""
        write_text(relative_path, existing + text, storage_mode)


def exists(relative_path: str, storage_mode: str) -> bool:
    _validate_mode(storage_mode)
    if storage_mode == "local":
        return (PROJECT_ROOT / relative_path).exists()
    return head_metadata(relative_path, storage_mode) is not None


def delete(relative_path: str, storage_mode: str) -> None:
    _validate_mode(storage_mode)
    if storage_mode == "local":
        local_path = PROJECT_ROOT / relative_path
        if local_path.exists():
            local_path.unlink()
    else:
        _s3_client().delete_object(Bucket=_bucket_name(), Key=relative_path)


def list_files(relative_prefix: str, storage_mode: str, suffix: str = "") -> list[str]:
    """Project-relative paths (local) or S3 keys (cloud) under a prefix, optionally filtered by suffix."""
    _validate_mode(storage_mode)
    if storage_mode == "local":
        base = PROJECT_ROOT / relative_prefix
        if not base.exists():
            return []
        return sorted(
            str(p.relative_to(PROJECT_ROOT))
            for p in base.rglob(f"*{suffix}")
            if p.is_file()
        )

    keys = []
    paginator = _s3_client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=_bucket_name(), Prefix=relative_prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(suffix):
                keys.append(obj["Key"])
    return sorted(keys)


def resolve_paths(entries: list[str], base_dir: str, storage_mode: str, suffix: str) -> list[str]:
    """
    Expands a mix of partition prefixes and exact file paths into a flat
    list of file paths to process. Each entry is either:
      - an exact file path (ends with `suffix`) — used as-is (prefixed
        with base_dir if not already a full path), so a caller that
        already knows exactly which files are new (e.g. the paths a
        refresh run just wrote) can target only those, without
        rescanning the rest of base_dir;
      - a directory/partition prefix (e.g. a country code, or a finer
        "country/year/pollutant" path) — expanded via list_files()
        under base_dir/entry, picking up every matching file currently
        there.

    Used by normalization/transformation run() functions that accept a
    `countries`-style argument, so "only the files that changed" and
    "everything under this country" are both expressible with the same
    parameter.
    """
    files = []
    for entry in entries:
        if entry.endswith(suffix):
            files.append(entry if entry.startswith(f"{base_dir}/") else f"{base_dir}/{entry}")
        else:
            files.extend(list_files(f"{base_dir}/{entry}", storage_mode, suffix=suffix))
    return files


def head_metadata(relative_path: str, storage_mode: str) -> dict | None:
    """
    {"size": int, "last_modified": datetime} for the current object, or
    None if it doesn't exist. This is metadata about *our own stored
    copy* (when we wrote it, how big it is) — not a signal about whether
    the original source changed; see common.change_tracking for that.
    """
    _validate_mode(storage_mode)
    if storage_mode == "local":
        local_path = PROJECT_ROOT / relative_path
        if not local_path.exists():
            return None
        stat = local_path.stat()
        return {
            "size": stat.st_size,
            "last_modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        }

    import botocore
    try:
        resp = _s3_client().head_object(Bucket=_bucket_name(), Key=relative_path)
    except botocore.exceptions.ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
            return None
        raise
    return {"size": resp["ContentLength"], "last_modified": resp["LastModified"]}
