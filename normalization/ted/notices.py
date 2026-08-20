"""
TED notices normalization.

Reads the raw notices JSONL saved by ingestion.ted.notices (full API
shape — all languages, `links`, everything) and reshapes each notice:
drops fields we don't use, and trims per-language dicts down to LANG +
English. No country filtering here — ingestion already scoped the query
to ISO3 server-side.

Reads/writes go through common.storage, so storage_mode="local" (default)
and storage_mode="cloud" (S3) run the same logic.

    from normalization.ted.notices import run
    run()
    run(storage_mode="cloud")
"""
import json
import logging
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.storage import exists, read_text, write_text

logger = logging.getLogger(__name__)

RAW_PATH = "data/raw/ted/notices.jsonl"
NORMALIZED_PATH = "data/normalized/ted/notices.jsonl"

LANG = "deu"  # buyer-name / notice-title kept in this language + English only


def strip_unwanted(notice: dict) -> dict:
    """
    TED attaches `links` (pdf/html/htmlDirect/xml × 23 languages) and
    sometimes stray email fields regardless of what we ask for — strip
    them out. Date fields are left untouched here and in trim_languages()
    below, whatever shape TED sends them in.
    """
    for key in list(notice.keys()):
        if key == "links" or "email" in key.lower():
            notice.pop(key)
    return notice


def trim_languages(notice: dict) -> dict:
    """
    buyer-name and notice-title come back with ~23 language keys each —
    keep only LANG + English.
    """
    for field in ("buyer-name", "notice-title"):
        value = notice.get(field)
        if isinstance(value, dict):
            notice[field] = {
                k: v for k, v in value.items() if k in (LANG, "eng")
            }
    return notice


def clean(notice: dict) -> dict:
    return trim_languages(strip_unwanted(notice))


def run(storage_mode: str = "local"):
    logger.info("Starting TED notices normalization | storage_mode=%s", storage_mode)

    if not exists(RAW_PATH, storage_mode):
        raise FileNotFoundError(
            f"No raw notices file at {RAW_PATH} — run ingestion.ted.notices first."
        )
    raw_text = read_text(RAW_PATH, storage_mode)

    read_count = 0
    written_lines = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        read_count += 1
        notice = clean(json.loads(line))
        written_lines.append(json.dumps(notice, ensure_ascii=False))

    write_text(NORMALIZED_PATH, "\n".join(written_lines) + ("\n" if written_lines else ""), storage_mode)

    logger.info("TED notices normalization finished | read=%s written=%s path=%s",
                read_count, len(written_lines), NORMALIZED_PATH)


if __name__ == "__main__":
    run(
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
    )
