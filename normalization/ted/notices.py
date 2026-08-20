"""
TED notices normalization.

Reads the raw notices JSONL saved by ingestion.ted.notices — one file
per country, data/raw/ted/<country>/notices.jsonl — and reshapes each
notice into one row of a compact analytical table, written to
data/normalized/ted/<country>/notices.parquet. This is a real schema
change from a straight raw copy: TED's response wraps almost every
field in a single-element list (even genuinely scalar ones like
buyer-country), multilingual fields as {lang: [value]} dicts, and mixes
NUTS + ISO3 country codes together inside place-of-performance — none of
that survives untouched here; each is resolved into a proper typed
column, based on real response shapes confirmed via live ingestion
output (2026-08-20), not guessed.

Key real-data findings that shaped this:
- Fields absent from a notice are OMITTED by TED entirely, not present
  as null/empty — every field access below is defensive (.get()).
- Almost all "scalar" business terms are still wrapped in a
  single-element list (e.g. "buyer-country": ["DEU"]) — unwrapped here.
- Multilingual fields (buyer-name, winner-name, notice-title,
  buyer-city) are {lang: [value, ...]} — resolved to one string per
  notice via LANG_PREFERENCE below, not kept as a nested structure.
  buyer-city has been observed keyed only by "mul" (TED's
  language-neutral tag, used for names that aren't translated) — with
  no "deu"/"eng" key at all, so "mul" is in the fallback chain, not
  just deu/eng.
- "place-of-performance" (not "BT-5071-Lot", which never appears as its
  own key in real responses even though it's what we request) mixes
  NUTS codes and ISO3 country codes in one flat list, e.g.
  ["DE236", "DEU"] — split apart by shape, see
  split_place_of_performance().
- "classification-cpv" can repeat the same code multiple times (one
  real notice listed each of 4 codes twice) — deduplicated here, order
  preserved.
- "total-value" is a bare float (e.g. 6054986.63) — unlike almost every
  other field, NOT list-wrapped.
- "non-award-justification" is a single-element list wrapping one code
  (e.g. ["ins-fund"]) — same shape as winner-selection-status.
- "green-procurement-criteria-lot" is a genuine multi-element list
  (e.g. ["other", "other"]) — one entry per lot the criterion applies
  to, so the same code can repeat; deduplicated the same way as
  classification-cpv, since this table is per-notice, not per-lot.

`country_code` is this project's own ISO2 code (from the file's own
directory) — a different code space from `buyer_country` (TED's own
ISO3 field, kept as returned).

If `countries` isn't passed, every country already ingested is
processed — read from the raw layer's own directory structure.

Reads/writes go through common.storage, so storage_mode="local"
(default) and storage_mode="cloud" (S3) run the same logic.

    from normalization.ted.notices import run
    run()
    run(countries=["DE", "PL"])
    run(storage_mode="cloud")
"""
import json
import logging
import re
import sys
from datetime import date
from io import BytesIO
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.storage import exists, list_files, read_text, write_bytes

logger = logging.getLogger(__name__)

RAW_BASE_DIR = "data/raw/ted"
NORMALIZED_BASE_DIR = "data/normalized/ted"
NOTICES_RAW_FILENAME = "notices.jsonl"
NOTICES_NORMALIZED_FILENAME = "notices.parquet"

# Multilingual fields come back as {lang: [value, ...]}; prefer German
# (this project's LANG elsewhere, and the language ingestion's FIELDS
# targets), then English, then TED's language-neutral "mul" tag (seen
# used for buyer-city, which usually isn't translated), then whatever's
# left.
LANG_PREFERENCE = ["deu", "eng", "mul"]

# A NUTS code is 2 letters + 1-3 digits (NUTS1 "DE7", NUTS2 "DE71",
# NUTS3 "DE712"); a bare ISO3 country code is 3 letters with no digit
# ("DEU") — the two shapes don't collide in practice.
NUTS_PATTERN = re.compile(r"^[A-Z]{2}[0-9]{1,3}$")
ISO3_PATTERN = re.compile(r"^[A-Z]{3}$")


def discover_countries(storage_mode: str) -> list[str]:
    """Country codes come from the raw layer's own <country>/ subdirectories."""
    raw_files = list_files(RAW_BASE_DIR, storage_mode, suffix=NOTICES_RAW_FILENAME)
    return sorted({Path(p).parent.name for p in raw_files})


# --------------------------------------------------------------------------
# Field-level helpers
# --------------------------------------------------------------------------

def unwrap_scalar(value):
    """
    TED wraps almost every field in a list, even genuinely scalar ones.
    A single-element list becomes its element; an empty list becomes
    None; a multi-element list is returned as-is (real multiplicity —
    not expected for the fields this is used on, but not discarded if
    it happens).
    """
    if isinstance(value, list):
        if len(value) == 1:
            return value[0]
        if len(value) == 0:
            return None
        return value
    return value


def resolve_language_field(value) -> str | None:
    """value is {lang: [str, ...]}. Picks one string using LANG_PREFERENCE,
    falling back to whatever language is present first. Multiple values
    for the chosen language are joined with "; "."""
    if not isinstance(value, dict) or not value:
        return None
    for lang in LANG_PREFERENCE:
        if value.get(lang):
            return "; ".join(value[lang])
    for values in value.values():
        if values:
            return "; ".join(values)
    return None


def parse_ted_date(value) -> date | None:
    """TED dates look like "2025-12-31+01:00" — a calendar date with a
    trailing UTC offset and no time component. Only the date matters
    here, so the offset is dropped rather than converted."""
    value = unwrap_scalar(value)
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        logger.warning("Could not parse TED date | raw=%s", value)
        return None


def split_place_of_performance(value) -> tuple[list[str], list[str]]:
    """
    place-of-performance mixes NUTS codes and ISO3 country codes in one
    flat list, e.g. ["DE236", "DEU"] (confirmed live, 2026-08-20) — not
    "BT-5071-Lot", which never appears as its own key in real responses
    even though it's what ingestion requests. Split by shape.
    """
    if not isinstance(value, list):
        value = [value] if value else []

    nuts_codes, country_codes = [], []
    for entry in value:
        if not isinstance(entry, str):
            continue
        if NUTS_PATTERN.match(entry):
            nuts_codes.append(entry)
        elif ISO3_PATTERN.match(entry):
            country_codes.append(entry)
        else:
            logger.warning("Unrecognized place-of-performance value | value=%s", entry)
    return nuts_codes, country_codes


def nuts_levels(code: str | None) -> tuple[str | None, str | None, str | None]:
    """NUTS codes nest by prefix: NUTS1 is the first 3 chars, NUTS2 the
    first 4, NUTS3 is the full 5-char code — only set if the matched
    code is actually that granular."""
    if not code:
        return None, None, None
    nuts1 = code[:3] if len(code) >= 3 else None
    nuts2 = code[:4] if len(code) >= 4 else None
    nuts3 = code if len(code) >= 5 else None
    return nuts1, nuts2, nuts3


def dedupe_preserve_order(values) -> list:
    if not values:
        return []
    if not isinstance(values, list):
        values = [values]
    return list(dict.fromkeys(values))


def extract_total_value(notice: dict) -> float | None:
    """
    Confirmed live (2026-08-20): a bare float, e.g. 6054986.63 — unlike
    almost every other field, NOT wrapped in a list. unwrap_scalar()
    still runs first as a no-op safety net in case that ever changes.
    """
    raw = unwrap_scalar(notice.get("total-value"))
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("Could not parse total-value | raw=%s", raw)
        return None


def extract_non_award_justification(notice: dict) -> str | None:
    """
    Confirmed live (2026-08-20): a single-element list wrapping one
    code, e.g. ["ins-fund"] — same shape as winner-selection-status.
    It's a controlled code list (BT-144, see ingestion.ted.codelists).
    """
    return unwrap_scalar(notice.get("non-award-justification"))


def extract_green_procurement_criteria(notice: dict) -> list[str]:
    """
    Confirmed live (2026-08-20): a genuine multi-element list, e.g.
    ["other", "other"] — one entry per lot, so the same code can repeat
    once per lot it applies to. Unlike non-award-justification, this
    isn't a single-value field to unwrap; deduplicated the same way as
    classification_cpv (this table is per-notice, not per-lot, so
    per-lot repetition isn't meaningful at this grain — the set of
    distinct criteria that apply to the notice is).
    """
    return dedupe_preserve_order(notice.get("green-procurement-criteria-lot"))


# --------------------------------------------------------------------------
# Notice -> row
# --------------------------------------------------------------------------

def flatten_notice(notice: dict, country: str) -> dict:
    nuts_codes, place_country_codes = split_place_of_performance(notice.get("place-of-performance"))
    primary_nuts = nuts_codes[0] if nuts_codes else None
    nuts1, nuts2, nuts3 = nuts_levels(primary_nuts)

    return {
        "country_code": country,
        "publication_number": notice.get("publication-number"),
        "notice_type": notice.get("notice-type"),
        "notice_title": resolve_language_field(notice.get("notice-title")),
        "publication_date": parse_ted_date(notice.get("publication-date")),
        "contract_conclusion_date": parse_ted_date(notice.get("contract-conclusion-date")),
        "buyer_name": resolve_language_field(notice.get("buyer-name")),
        "buyer_country": unwrap_scalar(notice.get("buyer-country")),
        "buyer_city": resolve_language_field(notice.get("buyer-city")),
        "buyer_post_code": unwrap_scalar(notice.get("buyer-post-code")),
        "winner_name": resolve_language_field(notice.get("winner-name")),
        "winner_selection_status": unwrap_scalar(notice.get("winner-selection-status")),
        "total_value": extract_total_value(notice),
        "total_value_currency": unwrap_scalar(notice.get("total-value-cur")),
        "classification_cpv": dedupe_preserve_order(notice.get("classification-cpv")),
        "non_award_justification": extract_non_award_justification(notice),
        "green_procurement_criteria": extract_green_procurement_criteria(notice),
        "nuts": primary_nuts,
        "nuts1": nuts1,
        "nuts2": nuts2,
        "nuts3": nuts3,
        "nuts_codes": nuts_codes,
        "place_of_performance_country": place_country_codes,
    }


def run(storage_mode: str = "local", countries: list[str] | None = None):
    countries = countries or discover_countries(storage_mode)
    if not countries:
        logger.warning("No raw notices files found under %s", RAW_BASE_DIR)
        return

    logger.info("Starting TED notices normalization | countries=%s storage_mode=%s", countries, storage_mode)

    for country in countries:
        raw_path = f"{RAW_BASE_DIR}/{country}/{NOTICES_RAW_FILENAME}"
        normalized_path = f"{NORMALIZED_BASE_DIR}/{country}/{NOTICES_NORMALIZED_FILENAME}"

        if not exists(raw_path, storage_mode):
            raise FileNotFoundError(
                f"No raw notices file at {raw_path} — run ingestion.ted.notices first."
            )

        rows = []
        for line in read_text(raw_path, storage_mode).splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(flatten_notice(json.loads(line), country))

        if not rows:
            logger.warning("No notices found in %s — nothing written for country=%s", raw_path, country)
            continue

        df = pd.DataFrame(rows)
        buffer = BytesIO()
        df.to_parquet(buffer, index=False)
        write_bytes(normalized_path, buffer.getvalue(), storage_mode)

        logger.info("TED notices normalization finished | country=%s rows=%s path=%s",
                    country, len(df), normalized_path)


if __name__ == "__main__":
    run(
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
        countries=["DE"],      # e.g. ["DE", "PL"] — omit/None to auto-discover from data/raw/ted/
    )
