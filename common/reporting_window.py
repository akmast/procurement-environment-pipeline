"""
EEA reporting-window math — which years are still "mutable" (worth
re-checking for changes) versus closed/stable, based on EEA's own
reporting lifecycle: verified (E1a) data for year Y is due from
countries by 30 September of year Y+1; until then, year Y's published
data (E2a/UTD) is still preliminary and may be corrected.

This is specific to EEA measurements — the one source in this project
that publishes as a redownloadable, revisable yearly snapshot rather
than an immutable stream of published records (see
docs/pipelines/eea_measurements.md). It is not meant to be reused for
TED, whose notices are treated as immutable once published and use their
own publication-number-based refresh logic instead
(ingestion.ted.notices's own "refresh" mode).

    from common.reporting_window import mutable_years, reporting_deadline
"""
from datetime import date


def reporting_deadline(reporting_year: int) -> date:
    """Verified (E1a) data for `reporting_year` is due by 30 September of the following year."""
    return date(reporting_year + 1, 9, 30)


def mutable_years(today: date | None = None) -> list[int]:
    """
    Years that should be automatically re-checked/refreshed, given
    `today` (defaults to the real current date):

      - the current calendar year — always, it's actively being reported
      - the previous calendar year — only while its own reporting
        deadline (30 September of the current year) hasn't passed yet

    Years older than that are considered closed and are deliberately
    excluded — they're a job for an explicit historical/backfill run,
    never automatic refresh.
    """
    today = today or date.today()
    current_year = today.year
    prior_year = current_year - 1

    years = [current_year]
    if today <= reporting_deadline(prior_year):
        years.append(prior_year)
    return years


def is_past_reporting_deadline(reporting_year: int, today: date | None = None) -> bool:
    today = today or date.today()
    return today > reporting_deadline(reporting_year)
