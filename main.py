"""
Single CLI entry point for the whole pipeline — the only thing the Docker
image actually runs (see Dockerfile's CMD). Every ingestion/normalization/
transformation module keeps its own importable Python API (`run(...)`)
unchanged; this file only calls into it and packages the result — no
business logic (API calls, parsing, dedup, joins, ...) lives here.

Commands:

  main.py stage --source SRC --stage {ingestion,normalization,transformation} ...
      Runs exactly one (source, stage). This is the atomic primitive
      Step Functions' ECS RunTask invokes — one call per state, one
      manifest written per call, at:
        local:  runs/<run_id>/<source>/<stage>.json
        cloud:  s3://<PIPELINE_S3_BUCKET>/runs/<run_id>/<source>/<stage>.json
      Input for a stage comes from exactly one of:
        --input-manifest URI   read a prior stage's own manifest and use
                                its written_paths (the normal AWS wiring —
                                normalization/transformation reprocess
                                only what actually changed upstream)
        --paths PATH [PATH...] an explicit list of partitions/files
        --countries CODE [CODE...] / --codelist-ids ID [ID...]
        --discover              process everything currently on disk
      An empty resolved input list is not an error — the stage completes
      as SKIPPED rather than reprocessing nothing.

  main.py pipeline {historical,update} --sources eea ted eurostat ...
      Local/manual convenience: runs every stage of the requested source
      families in dependency order, wiring each stage's written_paths
      into the next automatically. In AWS, Step Functions performs the
      equivalent sequencing as separate ECS tasks (see
      infrastructure/terraform/step_functions.tf) — this command exists
      for local testing and manual runs without deploying anything.
      Checks the bootstrap completion manifest first and refuses to run
      if reference data (NUTS boundaries, EEA stations, TED codelists)
      hasn't been prepared — see common/bootstrap.py.

  main.py bootstrap-reference --countries DE PL ...
      Local/manual convenience: runs the reference-data pipelines (NUTS
      boundaries, TED codelists, EEA stations) and writes the bootstrap
      completion manifest. Idempotent and re-runnable. Not run
      automatically by pipeline historical/update.

  main.py write-bootstrap-manifest
      Re-checks required reference outputs and (re)writes the bootstrap
      completion manifest — the atomic primitive Step Functions'
      BootstrapReferenceStateMachine calls as its final state.

Examples:

    python main.py stage --source eea-measurements --stage ingestion \\
        --mode historical --countries DE PL --from-year 2021 --to-year 2025 \\
        --storage-mode cloud --run-id my-run-1

    python main.py stage --source eea-measurements --stage normalization \\
        --input-manifest s3://my-bucket/runs/my-run-1/eea-measurements/ingestion.json \\
        --storage-mode cloud --run-id my-run-1

    python main.py pipeline historical --sources eea ted --countries DE PL \\
        --from-year 2021 --to-year 2025 --storage-mode cloud

    python main.py pipeline update --sources eea ted eurostat --storage-mode cloud

    python main.py bootstrap-reference --countries DE PL --storage-mode cloud

Exit code is 0 only when every stage actually run finished SUCCEEDED or
SKIPPED; a real failure (a stage's own StageResult.status == "FAILED", a
bootstrap check failure, or an uncaught exception) exits non-zero so
Step Functions/CI halts correctly — see common/manifest.py.
"""
import argparse
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone

_PROJECT_ROOT = __import__("pathlib").Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.bootstrap import check_bootstrap_complete, write_bootstrap_manifest
from common.logging_config import setup_logging
from common.manifest import StageResult, STATUS_FAILED
from common.storage import read_text, write_text

import ingestion.eea.nuts_boundaries as eea_nuts_boundaries_ingest
import ingestion.eea.stations as eea_stations_ingest
import ingestion.eea.measurements as eea_measurements_ingest
import ingestion.ted.codelists as ted_codelists_ingest
import ingestion.ted.notices as ted_notices_ingest
import ingestion.eurostat.agriculture_accounts as eurostat_agriculture_accounts_ingest

import normalization.eea.stations as eea_stations_norm
import normalization.eea.measurements as eea_measurements_norm
import normalization.ted.codelists as ted_codelists_norm
import normalization.ted.notices as ted_notices_norm
import normalization.eurostat.agriculture_accounts as eurostat_agriculture_accounts_norm

import transformation.eea.stations as eea_stations_transform
import transformation.eea.measurements as eea_measurements_transform
import transformation.ted.notices as ted_notices_transform

import gold.eea.measurements as eea_measurements_gold
import gold.ted.notices as ted_notices_gold
import gold.eurostat.agriculture_accounts as eurostat_agriculture_accounts_gold

logger = logging.getLogger(__name__)

VALID_SOURCES = [
    "eea-nuts-boundaries", "eea-stations", "eea-measurements",
    "ted-codelists", "ted-notices",
    "eurostat-agriculture-accounts",
]
VALID_STAGES = ["ingestion", "normalization", "transformation", "gold"]

# Sources with a Gold Layer build (see gold/<source>/*.py) — all three
# main-data families, never the reference-data sources (eea-nuts-boundaries,
# ted-codelists, eea-stations combine into other sources' Gold tables, not
# their own — see docs/pipelines/gold_layer.md).
GOLD_SOURCES = ["eea-measurements", "ted-notices", "eurostat-agriculture-accounts"]

# pipeline historical/update operate on source *families* (matching the
# Step Functions external input shape, e.g. {"sources": ["eea", "ted"]})
# — nuts_boundaries/stations/codelists are reference data, prepared only
# by bootstrap-reference, never by historical/update (see
# docs/aws/architecture.md).
FAMILY_TO_SOURCE = {
    "eea": "eea-measurements",
    "ted": "ted-notices",
    "eurostat": "eurostat-agriculture-accounts",
}
FAMILY_STAGES = {
    "eea-measurements": ["ingestion", "normalization", "transformation"],
    "ted-notices": ["ingestion", "normalization", "transformation"],
    "eurostat-agriculture-accounts": ["ingestion", "normalization"],
}


# --------------------------------------------------------------------------
# Input resolution — translates a prior stage's written_paths (or an
# explicit --paths/--countries/--discover) into the exact shape each
# run() expects. Some sources accept partition prefixes/exact file paths
# directly (eea-measurements, eurostat-agriculture-accounts); others
# store exactly one file per country/codelist and expect a bare code, so
# a prior stage's full written_paths need translating back to codes.
# --------------------------------------------------------------------------

def _resolve_paths_or_countries(paths, countries, discover, discover_fn, storage_mode):
    if paths:
        return paths
    if countries:
        return countries
    if discover:
        return discover_fn(storage_mode)
    raise ValueError("Provide one of --input-manifest, --paths, --countries, or --discover")


def _country_from_prefixed_path(path: str, base_dir: str) -> str:
    """<base_dir>/<country>/<anything> -> <country>."""
    relative = path[len(base_dir):].lstrip("/") if path.startswith(base_dir) else path
    return relative.split("/")[0]


def _resolve_country_codes(paths, countries, discover, discover_fn, storage_mode, translate_base_dir):
    if paths:
        return sorted({_country_from_prefixed_path(p, translate_base_dir) for p in paths})
    if countries:
        return countries
    if discover:
        return discover_fn(storage_mode)
    raise ValueError("Provide one of --input-manifest, --paths, --countries, or --discover")


def _codelist_id_from_path(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return name.removesuffix(".gc.xml").removesuffix(".parquet")


def _resolve_codelist_ids(paths, codelist_ids, discover, storage_mode):
    if paths:
        return sorted({_codelist_id_from_path(p) for p in paths})
    if codelist_ids:
        return codelist_ids
    if discover:
        return ted_codelists_norm.discover_codelist_ids(storage_mode)
    raise ValueError("Provide one of --input-manifest, --paths, --codelist-ids, or --discover")


def _load_paths_from_manifest(input_manifest: str, storage_mode: str) -> list[str]:
    key = input_manifest.split("/", 3)[3] if input_manifest.startswith("s3://") else input_manifest
    manifest = json.loads(read_text(key, storage_mode))
    return manifest.get("written_paths", [])


# --------------------------------------------------------------------------
# Dispatch — one (source, stage) at a time. No business logic here, only
# argument shaping: which run() to call and how to translate the
# resolved input into that run()'s actual parameter shape.
# --------------------------------------------------------------------------

def run_stage(*, source, stage, mode, storage_mode, countries, paths, discover,
              codelist_ids, from_year, to_year, from_date, to_date) -> StageResult:
    key = (source, stage)

    if key == ("eea-nuts-boundaries", "ingestion"):
        if countries:
            logger.warning("--countries ignored for eea-nuts-boundaries (EU-wide reference data)")
        return eea_nuts_boundaries_ingest.run(storage_mode=storage_mode)

    if key == ("eea-stations", "ingestion"):
        return eea_stations_ingest.run(mode=mode or "stations", storage_mode=storage_mode, countries=countries)
    if key == ("eea-stations", "normalization"):
        resolved = _resolve_country_codes(paths, countries, discover, eea_stations_norm.discover_countries,
                                           storage_mode, eea_stations_ingest.OUT_DIR)
        return eea_stations_norm.run(storage_mode=storage_mode, countries=resolved)
    if key == ("eea-stations", "transformation"):
        resolved = _resolve_country_codes(paths, countries, discover, eea_stations_transform.discover_countries,
                                           storage_mode, eea_stations_norm.NORMALIZED_BASE_DIR)
        return eea_stations_transform.run(storage_mode=storage_mode, countries=resolved)

    if key == ("eea-measurements", "ingestion"):
        return eea_measurements_ingest.run(mode=mode, storage_mode=storage_mode, countries=countries,
                                            from_year=from_year, to_year=to_year)
    if key == ("eea-measurements", "normalization"):
        resolved = _resolve_paths_or_countries(paths, countries, discover, eea_measurements_norm.discover_countries,
                                                storage_mode)
        return eea_measurements_norm.run(storage_mode=storage_mode, countries=resolved)
    if key == ("eea-measurements", "transformation"):
        resolved = _resolve_paths_or_countries(paths, countries, discover,
                                                eea_measurements_transform.discover_countries, storage_mode)
        return eea_measurements_transform.run(storage_mode=storage_mode, countries=resolved)

    if key == ("ted-codelists", "ingestion"):
        if countries:
            logger.warning("--countries ignored for ted-codelists (EU-wide reference data)")
        return ted_codelists_ingest.run(storage_mode=storage_mode)
    if key == ("ted-codelists", "normalization"):
        resolved = _resolve_codelist_ids(paths, codelist_ids, discover, storage_mode)
        return ted_codelists_norm.run(storage_mode=storage_mode, codelist_ids=resolved)

    if key == ("ted-notices", "ingestion"):
        return ted_notices_ingest.run(mode=mode, storage_mode=storage_mode, countries=countries,
                                       from_date=from_date, to_date=to_date)
    if key == ("ted-notices", "normalization"):
        resolved = _resolve_country_codes(paths, countries, discover, ted_notices_norm.discover_countries,
                                           storage_mode, ted_notices_ingest.OUT_DIR)
        return ted_notices_norm.run(storage_mode=storage_mode, countries=resolved)
    if key == ("ted-notices", "transformation"):
        resolved = _resolve_country_codes(paths, countries, discover, ted_notices_transform.discover_countries,
                                           storage_mode, ted_notices_norm.NORMALIZED_BASE_DIR)
        return ted_notices_transform.run(storage_mode=storage_mode, countries=resolved)

    if key == ("eurostat-agriculture-accounts", "ingestion"):
        return eurostat_agriculture_accounts_ingest.run(mode=mode, storage_mode=storage_mode, countries=countries,
                                                          from_year=from_year, to_year=to_year)
    if key == ("eurostat-agriculture-accounts", "normalization"):
        resolved = _resolve_paths_or_countries(paths, countries, discover,
                                                eurostat_agriculture_accounts_norm.discover_countries, storage_mode)
        return eurostat_agriculture_accounts_norm.run(storage_mode=storage_mode, countries=resolved)

    # Gold Layer — combines every country/year of the precursor stage's
    # output into one Parquet file (see gold/<source>/*.py's own
    # docstrings for exactly which precursor stage each reads and why).
    # Always used with --discover in practice (Step Functions' Gold
    # Standard state machine, see infrastructure/terraform/templates/
    # gold_standard.asl.json.tpl) since Gold's whole point is combining
    # everything currently available, not a specific changed subset.
    if key == ("eea-measurements", "gold"):
        resolved = _resolve_paths_or_countries(paths, countries, discover,
                                                eea_measurements_gold.discover_countries, storage_mode)
        return eea_measurements_gold.run(storage_mode=storage_mode, countries=resolved)
    if key == ("ted-notices", "gold"):
        resolved = _resolve_paths_or_countries(paths, countries, discover,
                                                ted_notices_gold.discover_countries, storage_mode)
        return ted_notices_gold.run(storage_mode=storage_mode, countries=resolved)
    if key == ("eurostat-agriculture-accounts", "gold"):
        resolved = _resolve_paths_or_countries(paths, countries, discover,
                                                eurostat_agriculture_accounts_gold.discover_countries, storage_mode)
        return eurostat_agriculture_accounts_gold.run(storage_mode=storage_mode, countries=resolved)

    raise ValueError(
        f"Unsupported (source, stage) combination: source={source!r} stage={stage!r} — "
        f"see main.py FAMILY_STAGES/VALID_SOURCES for what's available."
    )


# --------------------------------------------------------------------------
# Manifest assembly + execution wrapper — every command funnels through
# this so every stage run, regardless of which command invoked it,
# writes an identically-shaped manifest.
# --------------------------------------------------------------------------

def _build_period(from_year, to_year, from_date, to_date) -> dict | None:
    if from_year is not None or to_year is not None:
        return {"from_year": from_year, "to_year": to_year}
    if from_date is not None or to_date is not None:
        return {"from_date": from_date, "to_date": to_date}
    return None


def _execute_stage(*, source, stage, mode, storage_mode, run_id, countries, paths, discover,
                    codelist_ids, from_year, to_year, from_date, to_date, input_manifest):
    if input_manifest:
        paths = _load_paths_from_manifest(input_manifest, storage_mode)

    started_at = datetime.now(timezone.utc).isoformat()

    if stage in ("normalization", "transformation") and paths is not None and len(paths) == 0:
        logger.info("No input paths for source=%s stage=%s — nothing changed upstream, skipping", source, stage)
        result = StageResult().finalize(attempted=0)
    else:
        result = run_stage(
            source=source, stage=stage, mode=mode, storage_mode=storage_mode, countries=countries,
            paths=paths, discover=discover, codelist_ids=codelist_ids, from_year=from_year, to_year=to_year,
            from_date=from_date, to_date=to_date,
        )

    finished_at = datetime.now(timezone.utc).isoformat()

    manifest = result.to_dict()
    manifest.update({
        "run_id": run_id,
        "source": source,
        "stage": stage,
        "mode": mode,
        "countries": countries,
        "period": _build_period(from_year, to_year, from_date, to_date),
        "input_paths": paths or [],
        "started_at": started_at,
        "finished_at": finished_at,
    })

    manifest_relative_path = f"runs/{run_id}/{source}/{stage}.json"
    write_text(manifest_relative_path, json.dumps(manifest, ensure_ascii=False, indent=2), storage_mode)
    manifest_uri = (
        f"s3://{os.environ.get('PIPELINE_S3_BUCKET', '<PIPELINE_S3_BUCKET not set>')}/{manifest_relative_path}"
        if storage_mode == "cloud" else manifest_relative_path
    )
    logger.info("Manifest written | source=%s stage=%s status=%s written=%s failed=%s path=%s",
                source, stage, result.status, len(result.written_paths), len(result.failed_paths), manifest_uri)

    return result, manifest, manifest_uri


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_stage(args) -> int:
    countries = args.countries
    if args.countries_csv:
        countries = [c.strip() for c in args.countries_csv.split(",") if c.strip()]

    run_id = args.run_id or str(uuid.uuid4())
    result, _manifest, _uri = _execute_stage(
        source=args.source, stage=args.stage_name, mode=args.mode, storage_mode=args.storage_mode,
        run_id=run_id, countries=countries, paths=args.paths, discover=args.discover,
        codelist_ids=args.codelist_ids, from_year=args.from_year, to_year=args.to_year,
        from_date=args.from_date, to_date=args.to_date, input_manifest=args.input_manifest,
    )
    return 0 if result.status != STATUS_FAILED else 1


def cmd_pipeline(args) -> int:
    ingestion_mode = "historical" if args.pipeline_mode == "historical" else "refresh"
    if args.pipeline_mode == "historical" and (args.from_year is None or args.to_year is None):
        raise ValueError("pipeline historical requires --from-year and --to-year")

    check_bootstrap_complete(storage_mode=args.storage_mode)

    run_id = args.run_id or str(uuid.uuid4())
    overall_ok = True

    for family in args.sources:
        source = FAMILY_TO_SOURCE[family]
        paths = None
        for stage in FAMILY_STAGES[source]:
            if stage == "ingestion":
                is_historical = ingestion_mode == "historical"
                from_year = args.from_year if is_historical else None
                to_year = args.to_year if is_historical else None
                # TED's API is date-based, not year-based — the external
                # shape stays uniform (from_year/to_year) and gets
                # translated per-API here, in Python, not in Terraform/
                # Step Functions (see docs/aws/architecture.md).
                from_date = f"{args.from_year}-01-01" if (source == "ted-notices" and is_historical) else None
                to_date = f"{args.to_year}-12-31" if (source == "ted-notices" and is_historical) else None
                result, _manifest, uri = _execute_stage(
                    source=source, stage="ingestion", mode=ingestion_mode, storage_mode=args.storage_mode,
                    run_id=run_id, countries=args.countries, paths=None, discover=False, codelist_ids=None,
                    from_year=from_year, to_year=to_year, from_date=from_date, to_date=to_date,
                    input_manifest=None,
                )
            else:
                result, _manifest, uri = _execute_stage(
                    source=source, stage=stage, mode=ingestion_mode, storage_mode=args.storage_mode,
                    run_id=run_id, countries=None, paths=paths, discover=False, codelist_ids=None,
                    from_year=None, to_year=None, from_date=None, to_date=None, input_manifest=None,
                )

            logger.info("Pipeline stage finished | family=%s source=%s stage=%s status=%s manifest=%s",
                        family, source, stage, result.status, uri)

            if result.status == STATUS_FAILED:
                overall_ok = False
                logger.error("Halting %s pipeline after %s failure | run_id=%s", source, stage, run_id)
                break
            paths = result.written_paths

    return 0 if overall_ok else 1


def cmd_bootstrap_reference(args) -> int:
    run_id = args.run_id or str(uuid.uuid4())
    countries = args.countries or ["DE", "PL"]
    overall_ok = True

    def _run(source, stage, **kwargs):
        nonlocal overall_ok
        result, _manifest, uri = _execute_stage(
            source=source, stage=stage, mode=kwargs.get("mode"), storage_mode=args.storage_mode, run_id=run_id,
            countries=kwargs.get("countries"), paths=kwargs.get("paths"), discover=False, codelist_ids=None,
            from_year=None, to_year=None, from_date=None, to_date=None, input_manifest=None,
        )
        logger.info("Bootstrap step finished | source=%s stage=%s status=%s manifest=%s", source, stage,
                    result.status, uri)
        if result.status == STATUS_FAILED:
            overall_ok = False
        return result

    _run("eea-nuts-boundaries", "ingestion")

    codelists_ingest_result = _run("ted-codelists", "ingestion")
    if codelists_ingest_result.written_paths:
        _run("ted-codelists", "normalization", paths=codelists_ingest_result.written_paths)
    else:
        logger.error("No TED codelists were ingested — normalization skipped, bootstrap will be INCOMPLETE")
        overall_ok = False

    stations_result = _run("eea-stations", "ingestion", mode="stations", countries=countries)
    paths = stations_result.written_paths
    for stage in ("normalization", "transformation"):
        if not paths:
            logger.error("No EEA station data to run %s on — bootstrap will be INCOMPLETE", stage)
            overall_ok = False
            break
        stations_result = _run("eea-stations", stage, paths=paths)
        paths = stations_result.written_paths

    manifest = write_bootstrap_manifest(storage_mode=args.storage_mode)
    logger.info("Bootstrap-reference finished | run_id=%s status=%s missing=%s",
                run_id, manifest["status"], manifest["missing"])

    return 0 if (overall_ok and manifest["status"] == "COMPLETE") else 1


def cmd_write_bootstrap_manifest(args) -> int:
    manifest = write_bootstrap_manifest(storage_mode=args.storage_mode)
    return 0 if manifest["status"] == "COMPLETE" else 1


def cmd_check_bootstrap_complete(args) -> int:
    """
    Thin CLI wrapper around common.bootstrap.check_bootstrap_complete —
    exists so Step Functions can gate historical/update on bootstrap
    completion via a normal ECS RunTask state (same execution model as
    every other stage), instead of a separate direct-SDK integration
    whose S3 GetObject response shape isn't worth relying on unverified.
    """
    check_bootstrap_complete(storage_mode=args.storage_mode)
    return 0


# --------------------------------------------------------------------------
# argparse
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Procurement/environment pipeline — ingestion, normalization, transformation CLI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    stage_parser = subparsers.add_parser(
        "stage", help="Run exactly one (source, stage) — the primitive Step Functions/ECS RunTask invokes."
    )
    stage_parser.add_argument("--source", required=True, choices=VALID_SOURCES)
    stage_parser.add_argument("--stage", required=True, choices=VALID_STAGES, dest="stage_name")
    stage_parser.add_argument("--mode", default=None, help="test / historical / refresh / stations, depending on source")
    stage_parser.add_argument("--countries", nargs="+", default=None)
    stage_parser.add_argument("--countries-csv", default=None,
                               help="Comma-separated alternative to --countries, e.g. \"DE,PL\" — for callers "
                                    "that can't easily pass repeated argv tokens (Step Functions ASL has no "
                                    "array-join intrinsic, so state machine definitions use this instead)")
    stage_parser.add_argument("--paths", nargs="+", default=None,
                               help="Explicit partition prefixes or exact file paths (see common.storage.resolve_paths)")
    stage_parser.add_argument("--input-manifest", default=None,
                               help="URI/path of a prior stage's own manifest — its written_paths become this stage's input")
    stage_parser.add_argument("--codelist-ids", nargs="+", default=None)
    stage_parser.add_argument("--discover", action="store_true", help="Process everything currently on disk")
    stage_parser.add_argument("--from-year", type=int, default=None)
    stage_parser.add_argument("--to-year", type=int, default=None)
    stage_parser.add_argument("--from-date", default=None, help="YYYY-MM-DD")
    stage_parser.add_argument("--to-date", default=None, help="YYYY-MM-DD")
    stage_parser.add_argument("--storage-mode", choices=["local", "cloud"], default="local")
    stage_parser.add_argument("--run-id", default=None, help="Generated as a UUID4 if omitted")

    pipeline_parser = subparsers.add_parser(
        "pipeline", help="Run every stage of one or more source families in dependency order."
    )
    pipeline_parser.add_argument("pipeline_mode", choices=["historical", "update"])
    pipeline_parser.add_argument("--sources", nargs="+", choices=list(FAMILY_TO_SOURCE), default=list(FAMILY_TO_SOURCE))
    pipeline_parser.add_argument("--countries", nargs="+", default=None)
    pipeline_parser.add_argument("--from-year", type=int, default=None, help="Required for pipeline_mode=historical")
    pipeline_parser.add_argument("--to-year", type=int, default=None, help="Required for pipeline_mode=historical")
    pipeline_parser.add_argument("--storage-mode", choices=["local", "cloud"], default="local")
    pipeline_parser.add_argument("--run-id", default=None, help="Generated as a UUID4 if omitted")

    bootstrap_parser = subparsers.add_parser(
        "bootstrap-reference",
        help="Run the reference-data pipelines and write the bootstrap completion manifest.",
    )
    bootstrap_parser.add_argument("--countries", nargs="+", default=None, help="Default: DE PL")
    bootstrap_parser.add_argument("--storage-mode", choices=["local", "cloud"], default="local")
    bootstrap_parser.add_argument("--run-id", default=None, help="Generated as a UUID4 if omitted")

    write_manifest_parser = subparsers.add_parser(
        "write-bootstrap-manifest",
        help="Re-check required reference outputs and (re)write the bootstrap completion manifest.",
    )
    write_manifest_parser.add_argument("--storage-mode", choices=["local", "cloud"], default="local")

    check_bootstrap_parser = subparsers.add_parser(
        "check-bootstrap-complete",
        help="Verify the bootstrap completion manifest is COMPLETE — exits non-zero otherwise. "
             "Used by Step Functions as a gate before historical/update run any main-data stages.",
    )
    check_bootstrap_parser.add_argument("--storage-mode", choices=["local", "cloud"], default="local")

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging()

    try:
        if args.command == "stage":
            return cmd_stage(args)
        elif args.command == "pipeline":
            return cmd_pipeline(args)
        elif args.command == "bootstrap-reference":
            return cmd_bootstrap_reference(args)
        elif args.command == "write-bootstrap-manifest":
            return cmd_write_bootstrap_manifest(args)
        elif args.command == "check-bootstrap-complete":
            return cmd_check_bootstrap_complete(args)
        else:
            raise ValueError(f"Unknown command {args.command!r}")
    except Exception:
        logger.exception("main.py failed | command=%s", args.command)
        return 1


if __name__ == "__main__":
    sys.exit(main())
