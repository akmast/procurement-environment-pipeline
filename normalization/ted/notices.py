"""
TED notices normalization.

Reads the raw notices JSONL saved by ingestion.ted.notices — one file
per country, data/raw/ted/<country>/notices.jsonl — and reshapes each
notice into one row of a compact analytical table, written to
data/normalized/ted/<country>/notices.parquet. This is a real schema
change from a straight raw copy: TED's response wraps almost every
field in a single-element list, multilingual fields as {lang: [value]}
dicts, and mixes NUTS + ISO3 country codes together inside
place-of-performance — none of that survives untouched here; each is
resolved into a proper typed column, based on real response shapes
confirmed via live ingestion output (2026-08-20), not guessed.

Every output column has one stable dtype across all rows, never a mix
of a plain scalar and a list — pyarrow can't write that ("Expected
bytes, got a 'list' object"), and it silently corrupted buyer_country
before this was fixed. Two helpers enforce this per field (see below):
unwrap_required_scalar() for fields this project's data model treats as
genuinely single-valued (never leaves a list in the column — a
surprise multi-value notice gets its values joined into one string and
logged, not silently truncated to the first), and unwrap_multi() for
fields that can genuinely repeat (always a list, even for a single
value). validate_column_types() double-checks every column against
LIST_COLUMNS right before to_parquet(), so a violation fails loudly
with the specific column name and offending type instead of pyarrow's
less specific error.

Key real-data findings that shaped this:
- Fields absent from a notice are OMITTED by TED entirely, not present
  as null/empty — every field access below is defensive (.get()).
- Almost all "scalar" business terms are still wrapped in a
  single-element list (e.g. "total-value-cur": ["EUR"]) — unwrapped
  here. `buyer-country` looks the same shape (e.g. ["DEU"]) but is NOT
  always single-valued — a joint-procurement notice can list more than
  one buyer country, confirmed live — so it's kept as a list
  (unwrap_multi()), not unwrapped to a scalar.
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
ISO3 field(s), kept as a list of however many the notice returned).

`countries` must be passed explicitly — run() never defaults to scanning
and processing every country on disk. Pass discover_countries(storage_mode)
yourself to process everything currently ingested (it just lists the raw
layer's own <country>/ subdirectories) — that way "process everything"
is always a deliberate choice at the call site.

Reads/writes go through common.storage, so storage_mode="local"
(default) and storage_mode="cloud" (S3) run the same logic.

    from normalization.ted.notices import run, discover_countries
    run(countries=["DE", "PL"])
    run(countries=discover_countries("local"))
    run(countries=["DE"], storage_mode="cloud")
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

from common.manifest import StageResult
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

# A NUTS code is 2 letters (country) + 1-3 more letters/digits (NUTS1
# "DE7"/"DEA", NUTS2 "DE71"/"DED5", NUTS3 "DE712"/"DED51"/"PL22C") —
# most subdivisions are digits, but several countries use a letter at
# some level too (Germany's NUTS1 states run 1-9 then A-G; some
# countries' NUTS3 codes end in a letter, e.g. Poland's "PL22C"). A
# bare ISO3 country code ("DEU") is also 3 letters and can have the
# exact same shape as a 3-char letter-only NUTS1 code —
# split_place_of_performance() below checks ISO3_PATTERN first so a
# real ISO3 code is never misread as NUTS; the trade-off (unconfirmed
# in real data so far) is that a bare 3-letter NUTS1 code would be read
# as a country code instead.
NUTS_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{1,3}$")
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
    None; a multi-element list is returned **as a list** — this is the
    one case that must never reach a DataFrame column directly: it would
    leave that column holding a mix of plain scalars and lists, which
    pyarrow can't write ("Expected bytes, got a 'list' object"). Only
    used internally by parse_ted_date() (which explicitly handles the
    still-a-list case itself) and extract_total_value() (a no-op safety
    net on a field never observed list-wrapped). Every field written
    straight into flatten_notice()'s output row uses
    unwrap_required_scalar() or unwrap_multi() below instead — never
    this function directly.
    """
    if isinstance(value, list):
        if len(value) == 1:
            return value[0]
        if len(value) == 0:
            return None
        return value
    return value


def unwrap_required_scalar(value, field_name: str):
    """
    For fields this project's data model treats as genuinely
    single-valued per notice (e.g. notice_type, winner_selection_status
    — a controlled code, one per notice). Always returns a plain scalar
    or None — never a list — so the column stays a stable, single-typed
    string column even on the rare notice where TED sends more values
    than expected: rather than silently keeping only value[0] (discarding
    real data) or leaving a list in the column (breaking to_parquet()),
    every value is kept, joined into one string (same "; " convention as
    resolve_language_field's multi-value case), and logged so the
    anomaly isn't silently invisible.
    """
    if isinstance(value, list):
        if len(value) == 0:
            return None
        if len(value) == 1:
            return value[0]
        logger.warning("Expected a single value for %s, got %d — joining | raw=%s",
                        field_name, len(value), value)
        return "; ".join(str(v) for v in value)
    return value


def unwrap_multi(value) -> list:
    """
    For fields this project's data model treats as potentially
    multi-valued per notice (e.g. buyer_country — a joint-procurement
    notice can list more than one buyer country). Always returns a
    list — never a bare scalar — so the column stays list-typed even on
    a file where every notice in it happens to have exactly one value;
    a bare scalar becomes a single-element list, missing/empty becomes
    [].
    """
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


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
    here, so the offset is dropped rather than converted.

    Confirmed live: contract-conclusion-date occasionally arrives as
    several dates instead of one (a genuine multi-element list, not the
    usual single-element wrapping unwrap_scalar() unwraps) — the
    earliest of them is used. Anything that isn't a string or a list of
    strings (or fails to parse) is logged and treated as missing rather
    than raising, so one bad notice doesn't fail normalization for the
    whole country."""
    value = unwrap_scalar(value)
    if not value:
        return None
    if isinstance(value, list):
        parsed = [d for d in (parse_ted_date(v) for v in value) if d is not None]
        return min(parsed) if parsed else None
    if not isinstance(value, str):
        logger.warning("Unexpected TED date type | raw=%r type=%s", value, type(value).__name__)
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
        if ISO3_PATTERN.match(entry):
            country_codes.append(entry)
        elif NUTS_PATTERN.match(entry):
            nuts_codes.append(entry)
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
    return unwrap_required_scalar(notice.get("non-award-justification"), "non_award_justification")


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
        "publication_number": unwrap_required_scalar(notice.get("publication-number"), "publication_number"),
        "notice_type": unwrap_required_scalar(notice.get("notice-type"), "notice_type"),
        "notice_title": resolve_language_field(notice.get("notice-title")),
        "publication_date": parse_ted_date(notice.get("publication-date")),
        "contract_conclusion_date": parse_ted_date(notice.get("contract-conclusion-date")),
        "buyer_name": resolve_language_field(notice.get("buyer-name")),
        # Potentially multi-valued (a joint-procurement notice can list
        # more than one buyer country) — always a list, see unwrap_multi().
        "buyer_country": unwrap_multi(notice.get("buyer-country")),
        "buyer_city": resolve_language_field(notice.get("buyer-city")),
        "buyer_post_code": unwrap_required_scalar(notice.get("buyer-post-code"), "buyer_post_code"),
        "winner_name": resolve_language_field(notice.get("winner-name")),
        "winner_selection_status": unwrap_required_scalar(
            notice.get("winner-selection-status"), "winner_selection_status"
        ),
        "total_value": extract_total_value(notice),
        "total_value_currency": unwrap_required_scalar(notice.get("total-value-cur"), "total_value_currency"),
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


# Columns that must always hold a list (or None/NaN for a row with no
# data at all) — every other column must always hold a scalar. Checked
# by validate_column_types() right before to_parquet(), see run().
LIST_COLUMNS = frozenset({
    "buyer_country", "classification_cpv", "green_procurement_criteria",
    "nuts_codes", "place_of_performance_country",
})


def validate_column_types(df: pd.DataFrame) -> None:
    """
    Fails loudly, naming the exact column and the unexpected Python type,
    before to_parquet() ever sees inconsistent data — pyarrow's own
    ArrowTypeError names the column but not the offending value's actual
    type, which is exactly what made the original bug (a list sneaking
    into what pyarrow expected to be a plain string column) slow to
    root-cause. A LIST_COLUMNS column must hold only list (or None); any
    other column must never hold a list.
    """
    for column in df.columns:
        is_list_column = column in LIST_COLUMNS
        for value in df[column]:
            if value is None:
                continue
            if is_list_column and not isinstance(value, list):
                raise TypeError(
                    f"Column {column!r} is expected to hold lists, but contains "
                    f"{type(value).__name__}: {value!r}"
                )
            if not is_list_column and isinstance(value, list):
                raise TypeError(
                    f"Column {column!r} is expected to hold scalars, but contains a list: {value!r}"
                )


def run(storage_mode: str = "local", countries: list[str] | None = None) -> StageResult:
    if not countries:
        raise ValueError(
            "countries must be provided explicitly — e.g. countries=['DE'], or "
            "countries=discover_countries(storage_mode) to process every country "
            "already ingested. run() does not default to processing everything on disk."
        )

    logger.info("Starting TED notices normalization | countries=%s storage_mode=%s", countries, storage_mode)

    result = StageResult()
    for country in countries:
        raw_path = f"{RAW_BASE_DIR}/{country}/{NOTICES_RAW_FILENAME}"
        normalized_path = f"{NORMALIZED_BASE_DIR}/{country}/{NOTICES_NORMALIZED_FILENAME}"

        try:
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
                result.record_unchanged(raw_path)
                continue

            df = pd.DataFrame(rows)
            validate_column_types(df)
            buffer = BytesIO()
            df.to_parquet(buffer, index=False)
            write_bytes(normalized_path, buffer.getvalue(), storage_mode)
            result.record_written(normalized_path)

            logger.info("TED notices normalization finished | country=%s rows=%s path=%s",
                        country, len(df), normalized_path)
        except Exception:
            logger.exception("TED notices normalization failed | country=%s", country)
            result.record_failed(raw_path)

    return result.finalize(attempted=len(countries))


if __name__ == "__main__":
    run(
        storage_mode="local",  # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
        countries=["DE"],      # required — e.g. ["DE", "PL"], or discover_countries("local") for everything
    )
