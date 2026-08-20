# Storage modes, content hashing, and the EEA reporting window

Shared mechanics used across pipelines — described once here instead of
repeated in every pipeline doc. Each pipeline doc links back to this
page for the details.

## `local` vs `cloud`

Every `run(...)` across ingestion/normalization/transformation now takes
`storage_mode: str = "local"`:

- **`local`** — reads/writes the local filesystem, relative to the
  project root (not the current working directory — resolved from
  `common/storage.py`'s own file location, the same trick already used
  for `logs/`). Default. Used for development/testing, and always what
  the `if __name__ == "__main__":` examples in every module use.
- **`cloud`** — reads/writes S3, via the bucket named in the
  `PIPELINE_S3_BUCKET` environment variable. AWS credentials are picked
  up however `boto3` normally finds them (env vars, `~/.aws/credentials`,
  an instance role, etc.) — nothing project-specific to configure beyond
  the bucket name.

### Where the switching happens

One shared module, **`common/storage.py`**, exposes plain functions —
`read_bytes`, `write_bytes`, `read_text`, `write_text`, `append_text`,
`exists`, `list_files`, `head_metadata` — each taking a project-relative
path (e.g. `"data/raw/eea/stations/stations_raw.json"`) and the
`storage_mode`. Every pipeline module calls these instead of touching
`pathlib`/`open()`/`boto3` itself, so:

- the request/parsing/transformation logic is written **once** and never
  branches on `storage_mode`;
- switching a call from `local` to `cloud` changes *where bytes land*,
  never *what bytes are computed*.

For Parquet specifically, there's no special-casing either: a
`DataFrame` is written to an in-memory `BytesIO` buffer via
`df.to_parquet(buffer)`, and the buffer's bytes go through
`write_bytes()` like anything else — same code path for a local file or
an S3 object.

```
module code (ingestion/normalization/transformation)
        │  read_bytes(path, storage_mode) / write_bytes(path, content, storage_mode)
        ▼
common/storage.py
        │
   ┌────┴────┐
 local      cloud
   │           │
   ▼           ▼
filesystem   S3 (PIPELINE_S3_BUCKET)
```

## Content hashing — skip a write when nothing changed

**`common/change_tracking.py`** answers one question: *did the bytes we
just downloaded actually change since the last run?*

- `compute_hash(content)` — SHA-256 of the raw bytes. Content only —
  never file name, timestamp, or storage metadata (size, S3 `ETag`,
  `LastModified`), none of which reliably say anything about whether the
  content itself changed.
- The hash is kept in a small `state.json` next to the data it tracks
  (e.g. `data/raw/eea/stations/state.json`), itself read/written through
  `common.storage` — so state works the same way in `local` and `cloud`.
- Before writing anything, a module compares the freshly computed hash
  against the stored one (`has_changed()`). Same hash → skip the write
  entirely (and skip re-triggering downstream reprocessing of that
  file). Different hash, or nothing recorded yet → write, and record the
  new hash.

Applied to the sources that redownload a whole "snapshot" file each run
— **EEA stations**, **EEA measurements** (per Parquet file), **TED
codelists**. **Not** applied to TED notices: that source is an
append-only stream, already deduplicated per-record by
`publication-number` — there's no whole "file" to hash there, and it
already has its own correct incremental mechanism.

## The EEA reporting-window rule (30 September)

EEA publishes measurements as **E2a/UTD** (preliminary, continuously
reported) and, later, **E1a/verified** for a closed year. This project
only ever requests E2a (`dataset=1`). E2a values for a given year can
still be corrected by data providers up until the **verified data
deadline for that year**, which is **30 September of the following
year** (see the earlier research discussion — confirmed against the
European Air Quality Portal's own stated deadlines).

**`common/reporting_window.py`** turns that into one small, reusable
function:

```python
def mutable_years(today=None) -> list[int]:
    current_year = today.year
    prior_year = current_year - 1
    years = [current_year]                                    # always
    if today <= date(current_year, 9, 30):                     # prior year's own deadline
        years.append(prior_year)
    return years
```

- **Always** includes the current calendar year — it's actively being
  reported.
- Includes the **previous** year only while that year's own reporting
  deadline (30 September of the current year) hasn't passed yet.
- Years older than that are **never** auto-refreshed — that's a job for
  an explicit `historical` backfill run, not the incremental `refresh`.

`ingestion/eea/measurements.py`'s `mode="refresh"` calls
`mutable_years()` to decide which years to re-check — nothing is
hardcoded. For each year in the window, every pollutant is re-requested
for that full year (EEA's own files aren't reliably narrower than that —
see `docs/pipelines/eea_measurements.md`), and content hashing (above)
means a file whose data hasn't actually changed is left alone.

**This rule is EEA-measurements-specific.** It is not applied to EEA
stations (not a yearly reporting-cycle dataset), TED codelists (static
reference data, not a time series), or TED notices (immutable once
published — its own `publication-number` dedup already handles
incremental correctly, and there's no "still preliminary" period to
account for).

### Worked example

Today is **2026-08-19**. `mutable_years()` → current year `2026`, and
`2026-08-19 <= 2026-09-30` (the deadline for reporting year 2025) is
true, so `2025` is included too → `[2026, 2025]`.

- `mode="refresh"` re-requests **2026** and **2025** (all 5 pollutants,
  full year each) from the EEA API.
- Each returned file's bytes are hashed and compared to `state.json`.
  A station's 2025 file whose values a provider corrected last week
  → hash differs → rewritten, and normalization picks up the new
  values next run. A station's 2025 file nobody touched → hash matches
  → left untouched, nothing downstream needs to re-run for it.
- **2024 and earlier are not touched at all** by `refresh` — their
  reporting deadline (30 September 2025) is long past; getting new data
  for those years requires an explicit `run(mode="historical", from_year=2024, to_year=2024)`.

If today were **2026-10-15** instead, `2026-10-15 > 2026-09-30` →
`mutable_years()` would return just `[2026]` — 2025 has "closed" and
drops out of automatic refresh.
