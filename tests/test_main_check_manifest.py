"""Unit tests for main.py's check-manifest-has-output command — the gate
Step Functions uses to skip a Gold Layer rebuild when a historical/update
run didn't actually change that source's data (see
infrastructure/terraform/templates/historical.asl.json.tpl/update.asl.json.tpl)."""
import json

import pytest

import main


def _write_manifest(tmp_path, monkeypatch, run_id, source, stage, written_paths):
    monkeypatch.setattr("common.storage.PROJECT_ROOT", tmp_path)
    manifest_path = tmp_path / f"runs/{run_id}/{source}/{stage}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"written_paths": written_paths, "status": "SUCCEEDED"}), encoding="utf-8")


def _args(run_id, source, stage_name, storage_mode="local"):
    return main.build_parser().parse_args([
        "check-manifest-has-output", "--run-id", run_id, "--source", source,
        "--stage", stage_name, "--storage-mode", storage_mode,
    ])


def test_non_empty_written_paths_returns_zero(tmp_path, monkeypatch):
    _write_manifest(tmp_path, monkeypatch, "run1", "ted-notices", "normalization",
                     ["data/normalized/ted/DE/notices.parquet"])
    assert main.cmd_check_manifest_has_output(_args("run1", "ted-notices", "normalization")) == 0


def test_empty_written_paths_raises(tmp_path, monkeypatch):
    _write_manifest(tmp_path, monkeypatch, "run1", "ted-notices", "normalization", [])
    with pytest.raises(RuntimeError, match="nothing changed"):
        main.cmd_check_manifest_has_output(_args("run1", "ted-notices", "normalization"))


def test_missing_manifest_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("common.storage.PROJECT_ROOT", tmp_path)
    with pytest.raises(FileNotFoundError):
        main.cmd_check_manifest_has_output(_args("no-such-run", "ted-notices", "normalization"))


def test_eurostat_reads_normalization_stage_manifest(tmp_path, monkeypatch):
    """Eurostat has no transformation stage — the gate must be checkable
    against its normalization manifest directly."""
    _write_manifest(tmp_path, monkeypatch, "run1", "eurostat-agriculture-accounts", "normalization",
                     ["data/normalized/eurostat/regional_agricultural_accounts/DE/2021/aact_eaa01_r.parquet"])
    assert main.cmd_check_manifest_has_output(
        _args("run1", "eurostat-agriculture-accounts", "normalization")
    ) == 0
