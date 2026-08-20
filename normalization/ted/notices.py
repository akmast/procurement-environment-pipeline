"""
TED notices normalization.

Reads the raw notices JSONL saved by ingestion.ted.notices — one file per
country, data/raw/ted/<country>/notices.jsonl (full API shape — all
languages, `links`, everything) — and reshapes each notice: drops fields
we don't use, trims per-language dicts down to LANG + English, and stamps
an explicit `country_code` (ISO2, e.g. "DE") taken from the file's own
directory. No country *filtering* here — ingestion already scoped each
file's query to one country server-side; this only labels each notice
with the country its own file belongs to.

`country_code` is not the same code space as TED's own `buyer-country`
field (ISO3, e.g. "DEU") — both are kept: `buyer-country` untouched as
TED returned it, `country_code` as this project's own ISO2 convention,
known from the request that produced the file, not derived from
`buyer-country` by conversion.

If `countries` isn't passed, every country already ingested (i.e. every
subdirectory under data/raw/ted/) is processed.

Reads/writes go through common.storage, so storage_mode="local" (default)
and storage_mode="cloud" (S3) run the same logic.

    from normalization.ted.notices import run
    run()
    run(countries=["DE", "PL"])
    run(storage_mode="cloud")
"""
import json
import logging
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.storage import exists, list_files, read_text, write_text

logger = logging.getLogger(__name__)

RAW_BASE_DIR = "data/raw/ted"
NORMALIZED_BASE_DIR = "data/normalized/ted"
NOTICES_FILENAME = "notices.jsonl"

LANG = "deu"  # buyer-name / notice-title kept in this language + English only


def discover_countries(storage_mode: str) -> list[str]:
    """Country codes come from the raw layer's own <country>/ subdirectories."""
    raw_files = list_files(RAW_BASE_DIR, storage_mode, suffix=NOTICES_FILENAME)
    return sorted({Path(p).parent.name for p in raw_files})


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


def add_country_code(notice: dict, country: str) -> dict:
    """
    country_code is this project's own ISO2 code, known from the raw
    file's directory (i.e. the country ingestion actually requested) —
    not derived from TED's own `buyer-country` (a different code space,
    ISO3), and not touched.
    """
    notice["country_code"] = country
    return notice


def clean(notice: dict, country: str) -> dict:
    return add_country_code(trim_languages(strip_unwanted(notice)), country)


def run(storage_mode: str = "local", countries: list[str] | None = None):
    countries = countries or discover_countries(storage_mode)
    if not countries:
        logger.warning("No raw notices files found under %s", RAW_BASE_DIR)
        return

    logger.info("Starting TED notices normalization | countries=%s storage_mode=%s", countries, storage_mode)

    for country in countries:
        raw_path = f"{RAW_BASE_DIR}/{country}/{NOTICES_FILENAME}"
        normalized_path = f"{NORMALIZED_BASE_DIR}/{country}/{NOTICES_FILENAME}"

        if not exists(raw_path, storage_mode):
            raise FileNotFoundError(
                f"No raw notices file at {raw_path} — run ingestion.ted.notices first."
            )
        raw_text = read_text(raw_path, storage_mode)

        read_count = 0
        written_lines = []
        for line in raw_text.splitlines():
            line = line.strip()
            if not line:
                continue
            read_count += 1
            notice = clean(json.loads(line), country)
            written_lines.append(json.dumps(notice, ensure_ascii=False))

        write_text(normalized_path, "\n".join(written_lines) + ("\n" if written_lines else ""), storage_mode)

        logger.info("TED notices normalization finished | country=%s read=%s written=%s path=%s",
                    country, read_count, len(written_lines), normalized_path)


if __name__ == "__main__":
    run(
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
        countries=["DE"],      # e.g. ["DE", "PL"] — omit/None to auto-discover from data/raw/ted/
    )
