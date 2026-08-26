"""Unit tests for main.py's Gold Layer CLI wiring — run_stage() dispatch
for stage="gold", which is what HistoricalStateMachine/UpdateStateMachine
invoke per source right after that source's last data stage (see
infrastructure/terraform/templates/historical.asl.json.tpl,
update.asl.json.tpl)."""
import pytest

import main
from common.manifest import StageResult


@pytest.mark.parametrize("source,gold_module_name", [
    ("eea-measurements", "eea_measurements_gold"),
    ("ted-notices", "ted_notices_gold"),
    ("eurostat-agriculture-accounts", "eurostat_agriculture_accounts_gold"),
])
def test_gold_stage_dispatches_to_the_right_module_with_discovered_countries(monkeypatch, source, gold_module_name):
    gold_module = getattr(main, gold_module_name)
    calls = []

    def fake_discover_countries(storage_mode):
        return ["DE", "PL"]

    def fake_run(*, storage_mode, countries):
        calls.append({"storage_mode": storage_mode, "countries": countries})
        return StageResult().finalize(attempted=len(countries))

    monkeypatch.setattr(gold_module, "discover_countries", fake_discover_countries)
    monkeypatch.setattr(gold_module, "run", fake_run)

    result = main.run_stage(
        source=source, stage="gold", mode=None, storage_mode="cloud",
        countries=None, paths=None, discover=True, codelist_ids=None,
        from_year=None, to_year=None, from_date=None, to_date=None,
    )

    assert len(calls) == 1
    assert calls[0] == {"storage_mode": "cloud", "countries": ["DE", "PL"]}
    assert result.status != "FAILED"


def test_gold_stage_with_explicit_countries_skips_discovery(monkeypatch):
    calls = []
    monkeypatch.setattr(main.ted_notices_gold, "run",
                         lambda *, storage_mode, countries: calls.append(countries) or StageResult().finalize(1))

    main.run_stage(
        source="ted-notices", stage="gold", mode=None, storage_mode="local",
        countries=["DE"], paths=None, discover=False, codelist_ids=None,
        from_year=None, to_year=None, from_date=None, to_date=None,
    )

    assert calls == [["DE"]]


def test_gold_is_not_supported_for_reference_data_sources():
    with pytest.raises(ValueError, match="Unsupported"):
        main.run_stage(
            source="eea-stations", stage="gold", mode=None, storage_mode="local",
            countries=["DE"], paths=None, discover=False, codelist_ids=None,
            from_year=None, to_year=None, from_date=None, to_date=None,
        )


def test_gold_in_valid_stages():
    assert "gold" in main.VALID_STAGES
