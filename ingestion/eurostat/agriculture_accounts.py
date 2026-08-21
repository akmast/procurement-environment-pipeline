"""
Raw Eurostat regional agricultural accounts ingestion — test / historical /
refresh modes.

Downloads Eurostat's regional (NUTS2) Economic Accounts for Agriculture in
the API's own JSON-stat 2.0 representation, saved exactly as received — no
flattening, no dimension decoding, no filtering beyond country/year scope
requested server-side. That reshaping belongs to normalization.

Dataset: the former online data code `agr_r_accts` is no longer served by
the Statistics API (returns 404) — Eurostat's current replacement is
`aact_eaa01_r` ("Economic accounts for agriculture by NUTS 2 region -
values at current prices"), confirmed to exist via Eurostat's own product
catalogue and databrowser (outbound access to ec.europa.eu is blocked in
this sandbox, so the live API itself was never called during development —
see docs/pipelines/eurostat_agriculture_accounts.md for exactly what is
and isn't independently confirmed).

`geo=DE`/`geo=PL` select the national total, not that country's NUTS2
regions, and the API rejects a request that combines an explicit `geo`
value with `geoLevel=nuts2` (both cannot be set together) — so region
codes are discovered first with a small `geoLevel=nuts2` query, then the
real fact request sends those exact codes as repeated `geo` parameters.
Country filtering therefore happens server-side, not by discarding rows
after download.

Reads/writes go through common.storage, so storage_mode="local" (default)
and storage_mode="cloud" (S3) run the same logic. Every downloaded
response is staged, validated as a structurally valid JSON-stat 2.0
dataset, and only then hash-compared against what's already stored (see
common/staged_write.py) — a file is (re)written to final storage only if
it's both valid *and* changed.

    from ingestion.eurostat.agriculture_accounts import run
    run(mode="test")
    run(mode="historical", countries=["DE", "PL"], from_year=2021, to_year=2023)
    run(mode="refresh", countries=["DE", "PL"])
    run(mode="refresh", storage_mode="cloud")

Requires: pip install requests
"""
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make `common`/`ingestion` importable and logging configured regardless of
# how this file is run (python -m, import, Jupyter, or run directly).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.logging_config import setup_logging
from common.change_tracking import load_state, save_state
from common.manifest import StageResult
from common.staged_write import WRITE_RESULT_UNCHANGED, WRITE_RESULT_WRITTEN, stage_validate_and_write
from common.storage import write_bytes
from common.validation import is_valid_json_stat

try:
    from .http_client import request_with_retry
except ImportError:
    from http_client import request_with_retry

setup_logging()
logger = logging.getLogger(__name__)

DATASET_CODE = "aact_eaa01_r"
API_URL = f"https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{DATASET_CODE}"

OUT_DIR = "data/raw/eurostat/regional_agricultural_accounts"
TEST_DIR = "data/raw/eurostat/test/regional_agricultural_accounts"
DEFAULT_COUNTRIES = ["DE", "PL"]

# A small, single output series used only to discover which NUTS2 codes
# exist for a country and which year is the latest one actually published
# — never used to scope the real fact request, which pulls every am_item/
# indic_agr combination for the country/year (see fetch_country_year).
DISCOVERY_FILTERS = {
    "unit": "MIO_EUR",
    "am_item": "AM180000",  # Output of the agricultural industry
    "indic_agr": "PRD_BP",  # Production value at basic price
}


def country_state_path(country: str) -> str:
    return f"{OUT_DIR}/{country}/state.json"


def output_path(country: str, year: int) -> str:
    return f"{OUT_DIR}/{country}/{year}/{DATASET_CODE}.json"


def normalize_countries(countries: list[str]) -> list[str]:
    normalized = []
    for country in countries:
        code = country.strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", code):
            raise ValueError(f"Invalid country code {country!r} — expected ISO2, e.g. 'DE' or 'PL'")
        if code not in normalized:
            normalized.append(code)
    if not normalized:
        raise ValueError("countries must contain at least one ISO2 country code")
    return normalized


def validate_year_range(from_year: int, to_year: int) -> None:
    if from_year > to_year:
        raise ValueError(f"from_year ({from_year}) is after to_year ({to_year})")
    current_year = datetime.now(timezone.utc).year
    if from_year < 1900 or to_year > current_year:
        raise ValueError(f"Years must be between 1900 and {current_year}")


def _parse_json_stat(content: bytes) -> dict:
    try:
        payload = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError(f"Eurostat returned invalid JSON: {exc}") from exc

    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(f"Eurostat API error: {payload['error']}")
    if not isinstance(payload, dict) or payload.get("class") != "dataset":
        raise RuntimeError("Eurostat response is not a JSON-stat dataset")
    return payload


def get_json_stat(params: list[tuple[str, str]]) -> tuple[bytes, dict]:
    resp = request_with_retry("GET", API_URL, params=params)
    payload = _parse_json_stat(resp.content)
    logger.info("Eurostat response received | status=%s size_bytes=%s updated=%s",
                resp.status_code, len(resp.content), payload.get("updated"))
    return resp.content, payload


def category_codes(payload: dict, dimension: str) -> list[str]:
    """
    Codes for one dimension, in *position* order (position 0 first).
    JSON-stat 2.0's category.index is either an array (position = array
    index) or an object mapping {code: position} — for the object form,
    dict key order is not guaranteed by the spec to match position order,
    so codes are always resolved by sorting on the position value, never
    by assuming iteration order.
    """
    try:
        index = payload["dimension"][dimension]["category"]["index"]
    except KeyError as exc:
        raise RuntimeError(f"Eurostat response has no {dimension!r} category index") from exc

    if isinstance(index, list):
        return index
    if isinstance(index, dict):
        return [code for code, _position in sorted(index.items(), key=lambda item: item[1])]
    raise RuntimeError(f"Unexpected JSON-stat category index shape for {dimension!r}: {type(index)}")


def codes_with_observations(payload: dict, dimension: str) -> list[str]:
    """
    Codes for one dimension that participate in at least one non-null
    value cell. JSON-stat's `value` is a flat array (or, more commonly for
    sparse cubes, a {flat_index: value} object) over the dimensions listed
    in `id`, in row-major order — the last dimension in `id` varies
    fastest. A dimension's `stride` is therefore the product of the sizes
    of every dimension listed after it; a flat index's category position
    for that dimension is `(flat_index // stride) % size`.
    """
    dimension_ids = payload["id"]
    sizes = payload["size"]
    position = dimension_ids.index(dimension)
    stride = 1
    for size in sizes[position + 1:]:
        stride *= size

    codes = category_codes(payload, dimension)
    raw_values = payload.get("value", {})
    flat_indexes = (
        raw_values.keys() if isinstance(raw_values, dict)
        else (i for i, v in enumerate(raw_values) if v is not None)
    )
    observed_positions = {(int(flat_index) // stride) % sizes[position] for flat_index in flat_indexes}
    return [code for i, code in enumerate(codes) if i in observed_positions]


def discover_nuts2_codes(country: str, year: int) -> list[str]:
    """NUTS2 region codes belonging to `country`, from a small server-side
    `geoLevel=nuts2` query — geo=<country> alone would return the national
    total, not the NUTS2 regions, and geo/geoLevel cannot both be set."""
    params = [("lang", "EN"), ("time", str(year)), ("geoLevel", "nuts2")]
    params.extend(DISCOVERY_FILTERS.items())
    _, payload = get_json_stat(params)

    codes = [code for code in category_codes(payload, "geo") if code.startswith(country)]
    if not codes:
        raise RuntimeError(
            f"No NUTS2 regions found for country={country} year={year} — "
            f"the year may not be published for this country yet."
        )
    if len(codes) > 50:
        raise RuntimeError(
            f"Country {country} has {len(codes)} NUTS2 codes; the API accepts "
            f"at most 50 values per dimension filter."
        )

    logger.info("NUTS2 regions discovered | country=%s year=%s regions=%s", country, year, len(codes))
    return codes


def discover_latest_year(country: str) -> int:
    """
    Latest year with an actual observation for this country's NUTS2
    regions. Not hardcoded and not just "the newest year in the
    dimension": Eurostat's `time` dimension can list a year even when a
    specific country has no submitted data for it yet (other countries may
    already have it) — so this checks value presence, scoped to this
    country's own region codes, not just dimension membership.
    """
    discovery_params = [("lang", "EN"), ("lastTimePeriod", "5"), ("geoLevel", "nuts2")]
    discovery_params.extend(DISCOVERY_FILTERS.items())
    _, discovery_payload = get_json_stat(discovery_params)

    country_codes = [
        code for code in codes_with_observations(discovery_payload, "geo") if code.startswith(country)
    ]
    if not country_codes:
        raise RuntimeError(
            f"Could not discover the latest available year for country={country} — "
            f"no NUTS2 regions for this country have recent observations."
        )

    # Re-query scoped to exactly this country's codes so a year that's only
    # available for OTHER countries can't look available here.
    country_params = [("lang", "EN"), ("lastTimePeriod", "5")]
    country_params.extend(DISCOVERY_FILTERS.items())
    country_params.extend(("geo", code) for code in country_codes)
    _, country_payload = get_json_stat(country_params)

    years = [int(year) for year in codes_with_observations(country_payload, "time")]
    if not years:
        raise RuntimeError(f"Could not discover the latest available year for country={country}")

    latest_year = max(years)
    logger.info("Latest Eurostat year discovered | country=%s year=%s", country, latest_year)
    return latest_year


def fetch_country_year(country: str, year: int) -> tuple[bytes, dict, int]:
    """Fetch the full regional agricultural accounts cube (every am_item/
    indic_agr combination) for one country and year."""
    geo_codes = discover_nuts2_codes(country, year)
    params = [("lang", "EN"), ("time", str(year)), ("unit", "MIO_EUR")]
    params.extend(("geo", code) for code in geo_codes)

    logger.info("Requesting agricultural accounts | country=%s year=%s regions=%s dataset=%s",
                country, year, len(geo_codes), DATASET_CODE)
    content, payload = get_json_stat(params)

    response_codes = set(category_codes(payload, "geo"))
    if response_codes != set(geo_codes):
        raise RuntimeError(
            f"Eurostat response region mismatch for country={country} year={year}: "
            f"requested={len(geo_codes)} returned={len(response_codes)}"
        )
    return content, payload, len(geo_codes)


def _save_country_year(country: str, year: int, state: dict, storage_mode: str) -> tuple[str, str]:
    """Returns (write_result, path). write_result is one of
    WRITE_RESULT_WRITTEN / WRITE_RESULT_UNCHANGED / WRITE_RESULT_INVALID
    (see common.staged_write). Raises on fetch/discovery failure — the
    caller isolates that per year via try/except."""
    content, payload, region_count = fetch_country_year(country, year)
    path = output_path(country, year)
    write_result = stage_validate_and_write(path, content, storage_mode, state, validate=is_valid_json_stat)

    if write_result == WRITE_RESULT_WRITTEN:
        state[path].update({
            "dataset": DATASET_CODE,
            "source_updated": payload.get("updated"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "year": year,
            "country": country,
            "region_count": region_count,
        })
        logger.info("Agricultural accounts saved | country=%s year=%s regions=%s size_bytes=%s path=%s",
                    country, year, region_count, len(content), path)
    elif write_result == WRITE_RESULT_UNCHANGED:
        logger.info("Agricultural accounts unchanged | country=%s year=%s path=%s", country, year, path)
    else:
        logger.error("Agricultural accounts not written (invalid) | country=%s year=%s path=%s", country, year, path)

    return write_result, path


def tracked_years(state: dict, country: str) -> list[int]:
    prefix = f"{OUT_DIR}/{country}/"
    years = set()
    for path, metadata in state.items():
        if not path.startswith(prefix):
            continue
        year = metadata.get("year") if isinstance(metadata, dict) else None
        if isinstance(year, int):
            years.add(year)
            continue
        match = re.search(rf"^{re.escape(prefix)}(\d{{4}})/", path)
        if match:
            years.add(int(match.group(1)))
    return sorted(years)


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------

def run_test(countries: list[str], storage_mode: str) -> StageResult:
    """One output series, latest available year per country. Doesn't touch
    real storage or state. A failed country is isolated (logged + recorded
    in failed_paths) and doesn't stop the rest of the batch."""
    logger.info("Starting Eurostat agricultural accounts ingestion | mode=test countries=%s storage_mode=%s",
                countries, storage_mode)
    result = StageResult()
    for country in countries:
        path = f"{TEST_DIR}/{country}"
        try:
            latest_year = discover_latest_year(country)
            geo_codes = discover_nuts2_codes(country, latest_year)

            params = [("lang", "EN"), ("time", str(latest_year))]
            params.extend(DISCOVERY_FILTERS.items())
            params.extend(("geo", code) for code in geo_codes)
            content, payload = get_json_stat(params)

            observed_codes = codes_with_observations(payload, "geo")
            if not observed_codes:
                raise RuntimeError(f"Test response has no observations | country={country} year={latest_year}")
            if not is_valid_json_stat(content):
                raise RuntimeError(f"Test response failed JSON-stat validation | country={country}")

            path = f"{TEST_DIR}/{country}/{latest_year}/{DATASET_CODE}.json"
            write_bytes(path, content, storage_mode)
            result.record_written(path)
            logger.info("Test file saved | country=%s year=%s regions=%s path=%s",
                        country, latest_year, len(observed_codes), path)
        except Exception:
            logger.exception("Test ingestion failed | country=%s", country)
            result.record_failed(path)

    return result.finalize(attempted=len(countries))


def run_historical(countries: list[str], from_year: int, to_year: int, storage_mode: str) -> StageResult:
    """Returns a StageResult merged across every requested country and
    year — written_paths is every file that changed this run, ready to
    pass into normalization.eurostat.agriculture_accounts.run(countries=...)
    (see docs/pipelines/countries.md). A failed year is isolated (logged +
    recorded in failed_paths) and doesn't stop the rest of the batch."""
    validate_year_range(from_year, to_year)
    logger.info("Starting Eurostat agricultural accounts ingestion | mode=historical countries=%s "
                "years=%s-%s storage_mode=%s", countries, from_year, to_year, storage_mode)

    result = StageResult()
    for country in countries:
        state = load_state(country_state_path(country), storage_mode)
        country_result = StageResult()
        for year in range(from_year, to_year + 1):
            path = output_path(country, year)
            try:
                write_result, path = _save_country_year(country, year, state, storage_mode)
                if write_result == WRITE_RESULT_WRITTEN:
                    country_result.record_written(path)
                elif write_result == WRITE_RESULT_UNCHANGED:
                    country_result.record_unchanged(path)
                else:
                    country_result.record_failed(path)
            except Exception:
                logger.exception("Agricultural accounts ingestion failed | country=%s year=%s", country, year)
                country_result.record_failed(path)
        save_state(country_state_path(country), state, storage_mode)

        attempted = (len(country_result.written_paths) + len(country_result.unchanged_paths)
                     + len(country_result.failed_paths))
        country_result.finalize(attempted=attempted)
        logger.info("Historical ingestion finished | country=%s years=%s-%s written=%s unchanged=%s failed=%s",
                    country, from_year, to_year, len(country_result.written_paths),
                    len(country_result.unchanged_paths), len(country_result.failed_paths))
        result.merge(country_result)

    attempted = len(result.written_paths) + len(result.unchanged_paths) + len(result.failed_paths)
    return result.finalize(attempted=attempted)


def run_refresh(countries: list[str], storage_mode: str, from_year: int | None = None) -> StageResult:
    """
    Re-checks every year already tracked for the country, plus any newly
    published year, and only rewrites a file when its content hash
    actually changed. Eurostat publishes annual snapshots and can revise
    already-published observations — there's no append-only record stream
    or "created_at" cursor to follow, so this re-requests every tracked
    year rather than guessing which old year might have been revised
    (DE/PL's yearly subsets are small enough that this is cheap). A
    failed year is isolated (logged + recorded in failed_paths) and
    doesn't stop the rest of the batch.
    """
    logger.info("Starting Eurostat agricultural accounts ingestion | mode=refresh countries=%s "
                "storage_mode=%s", countries, storage_mode)

    result = StageResult()
    for country in countries:
        state = load_state(country_state_path(country), storage_mode)
        tracked = tracked_years(state, country)
        if not tracked and from_year is None:
            raise RuntimeError(
                f"No historical state found for country={country} — run mode='historical' first, "
                f"or pass from_year explicitly to bound a first refresh."
            )

        latest_year = discover_latest_year(country)
        first_year = from_year if from_year is not None else min(tracked)
        validate_year_range(first_year, latest_year)
        years = list(range(first_year, latest_year + 1))
        logger.info("Refresh window resolved | country=%s tracked_years=%s latest_available=%s "
                    "refresh_years=%s", country, tracked, latest_year, years)

        country_result = StageResult()
        for year in years:
            path = output_path(country, year)
            try:
                write_result, path = _save_country_year(country, year, state, storage_mode)
                if write_result == WRITE_RESULT_WRITTEN:
                    country_result.record_written(path)
                elif write_result == WRITE_RESULT_UNCHANGED:
                    country_result.record_unchanged(path)
                else:
                    country_result.record_failed(path)
            except Exception:
                logger.exception("Agricultural accounts refresh failed | country=%s year=%s", country, year)
                country_result.record_failed(path)
        save_state(country_state_path(country), state, storage_mode)

        attempted = (len(country_result.written_paths) + len(country_result.unchanged_paths)
                     + len(country_result.failed_paths))
        country_result.finalize(attempted=attempted)
        logger.info("Refresh finished | country=%s written=%s unchanged=%s failed=%s",
                    country, len(country_result.written_paths),
                    len(country_result.unchanged_paths), len(country_result.failed_paths))
        result.merge(country_result)

    attempted = len(result.written_paths) + len(result.unchanged_paths) + len(result.failed_paths)
    return result.finalize(attempted=attempted)


def run(mode: str, storage_mode: str = "local", countries: list[str] | None = None,
        from_year: int | None = None, to_year: int | None = None) -> StageResult:
    """
    Returns a common.manifest.StageResult — written_paths is every file
    that changed this run, ready to pass into
    normalization.eurostat.agriculture_accounts.run(countries=...) so a
    refresh only reprocesses new/revised data (see docs/pipelines/countries.md).
    """
    countries = normalize_countries(countries or DEFAULT_COUNTRIES)
    if mode == "test":
        return run_test(countries, storage_mode)
    elif mode == "historical":
        if from_year is None or to_year is None:
            raise ValueError("historical mode requires both from_year and to_year")
        return run_historical(countries, from_year, to_year, storage_mode)
    elif mode == "refresh":
        return run_refresh(countries, storage_mode, from_year=from_year)
    else:
        raise ValueError(f"Unknown mode {mode!r} — expected 'test', 'historical', or 'refresh'")


if __name__ == "__main__":
    run(
        mode="historical",       # Run mode: test, historical, or refresh
        storage_mode="local",    # "local" for development/testing, "cloud" for S3 (PIPELINE_S3_BUCKET)
        countries=["DE", "PL"],
        from_year=2021,          # Start year for historical mode
        to_year=2023,            # End year for historical mode
    )
