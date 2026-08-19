"""
TED notices normalization.

Reads the raw notices JSONL saved by ingestion.ted.notices (full API
shape — all languages, `links`, everything) and reshapes each notice:
drops fields we don't use, and trims per-language dicts down to LANG +
English. No country filtering here — ingestion already scoped the query
to ISO3 server-side.

    from normalization.ted.notices import run
    run()
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

RAW_PATH = Path("data/raw/ted/notices.jsonl")
OUT_DIR = Path("data/normalized/ted")
OUT_DIR.mkdir(parents=True, exist_ok=True)
NORMALIZED_PATH = OUT_DIR / "notices.jsonl"

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


def run():
    logger.info("Starting TED notices normalization")
    if not RAW_PATH.exists():
        raise FileNotFoundError(
            f"No raw notices file at {RAW_PATH} — run ingestion.ted.notices first."
        )

    read_count = 0
    written_count = 0
    with open(RAW_PATH, "r", encoding="utf-8") as raw_file, \
            open(NORMALIZED_PATH, "w", encoding="utf-8") as out_file:
        for line in raw_file:
            line = line.strip()
            if not line:
                continue
            read_count += 1
            notice = clean(json.loads(line))
            out_file.write(json.dumps(notice, ensure_ascii=False) + "\n")
            written_count += 1

    logger.info("TED notices normalization finished | read=%s written=%s path=%s",
                read_count, written_count, NORMALIZED_PATH.resolve())


if __name__ == "__main__":
    run()
