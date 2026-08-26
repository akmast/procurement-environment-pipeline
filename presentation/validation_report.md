# Validation Report — Procurement & Environment Data Pipeline Deck

## Generated files

```
presentation/
├── Procurement_Environment_Data_Pipeline.pptx   (principal deliverable, editable)
├── build_presentation.js                         (entry point — palette, layout helpers, page shell)
├── slides/slide01.js … slide14.js                (one module per slide, required by build_presentation.js)
├── package.json / package-lock.json / node_modules/  (pptxgenjs + rendering deps — `npm install` already run)
├── rendered/slide-01.png … slide-14.png           (150 DPI PNG render of every slide)
└── validation_report.md                           (this file)
```

Regenerate with: `cd presentation && node build_presentation.js` (writes the `.pptx`; rerun the LibreOffice/`pdftoppm` commands below to refresh the PDF/PNGs).

## Slide count

**14 slides**, confirmed three independent ways: `python-pptx` (`len(Presentation(...).slides)`), `markitdown` (`Slide number:` marker count), and the PNG count in `rendered/`.

## Rendering method

- `node build_presentation.js` → `.pptx` (PptxGenJS, `LAYOUT_WIDE` 13.333″×7.5″).
- `soffice --headless --convert-to pdf` (LibreOffice Impress) → `.pdf`. **LibreOffice Impress/Draw were not installed in this sandbox at session start** — only `libreoffice-core`/`-common` were present, so the first conversion attempt failed with "source file could not be loaded" (reproduced even on a trivial one-slide test file and a plain `.txt`, confirming it wasn't a defect in the generated deck). Installed `libreoffice-impress` via `apt-get` (pulled from the already-configured `archive.ubuntu.com` mirror) mid-session; conversion succeeded afterward.
- `pdftoppm -png -r 150` (Poppler, also installed mid-session) → `rendered/slide-NN.png`.
- Structural validation: `scripts/office/validate.py` (schema/relationship/content-type/chart/slide checks) — **all validations PASSED**, run against the final build.
- Content validation: `markitdown` text dump, grepped for leftover placeholder markers (`TODO`, `lorem`, `[insert`, `XXX`, etc.) — **none found**.

## Overlap / overflow check result

Every one of the 14 slides was rendered to PNG and visually inspected at least twice (an initial pass, then a second full pass after the global font-size compliance fix described below). Defects found and fixed across iterations:

- Slide 1: "City haze" bar illustration overflowed into the card above it (bar heights exceeded the card's available space) — fixed by scaling bar heights to the card interior. Problem-statement text overflowed its card — fixed by removing artificial blank-line spacing.
- Slide 2: stakeholder-card sub-text sat flush against the card's bottom edge — added padding.
- Slide 3: JSON code blocks were sized for a fitted-line-height estimate that ran short by about one line at 16pt Courier New, so closing braces clipped past the block — fixed by recalculating block height from actual line count with a corrected per-line constant; also shortened two JSON fields that would otherwise have wrapped.
- Slide 4: the entire vertical layer stack overflowed past the slide footer, and the arrow-connector labels rendered at zero length (an off-by-one in the arrow's start/end y-coordinates), plus 2-line-wrapped side-callout text collided with the next row. Rebuilt the layer node/callout spacing, fixed the arrow coordinate bug (also present on slide 5/6, see below), and shortened callouts to one line each.
- Slide 5 / 6: connector arrows between steps rendered as a bare arrowhead with no visible line — traced to the same off-by-one (`y2 - gap` instead of `y2`) in the arrow helper; fixed once, which corrected both slides.
- Slide 7: this was the most-revised slide — controller-strip pills clipped their own two-line text, per-lane processing/codelist caption lines overflowed into the sample box below, and the bottom merge/notes row used fixed y-coordinates that collided with the tallest lane's actual content. Rebuilt with a lane-header module-name field (removing a repeated "RUN" line from every stage card), and made the merge/notes row's y-position a function of `Math.max()` over all three lanes' actual computed heights instead of a hardcoded constant.
- Slide 8 / 9: source-lane module-name subtitles wrapped inside their header badges; widened that text region. Slide 9 specifically: a `state.json` example line collided with the box above it, one lane's "RE-QUERY" text silently wrapped to a 3rd line inside a 2-line-tall box (ghosting into the row below), and the bottom rule banner text wrapped past its box — all fixed by shortening the specific strings and correcting box heights.
- Slide 10: two Gold-table schema-sample blocks (TED, Eurostat) had a column pair packed onto one line that exceeded the block width at 16pt; split onto separate lines. A hardcoded path-list block height didn't grow when the Eurostat path needed to wrap to two lines. The "Gold principles" bullet list overflowed the footer — tightened list spacing.
- Slide 11: the Glue Data Catalog node's subtitle wrapped inside its own title area — shortened it.
- Slide 12: the "Country: DE | PL" filter-pill text overflowed a too-narrow pill — widened it.
- Slide 13: the Quality table's third row was hidden behind the "EEA Gold" callout card below it because the assumed table height under-counted actual rendered row height; the callout cards themselves wrapped their body line inside a too-short box; the "Limitations" bullets wrapped inside a too-narrow two-column layout. Rebuilt with a wider height buffer under the table, taller callout cards, and a single full-width limitations column.
- Slide 14: no defects found on inspection.

**A second, dedicated pass** was run specifically for the 16pt body-text minimum (see below), which surfaced further sub-16pt text (arrow-connector captions, node subtitles, pill labels, one domain-callout paragraph) that had rendered without visual overlap at the smaller size but violated the stated minimum; raising those to 16pt reintroduced several of the same class of overflow on slides 4, 10, and 13, which were fixed again and re-verified by a fourth render pass. The version in this repository is the final, re-verified one.

## Repository files inspected

Read directly from `origin/main` (this session's working tree already matched `origin/main` exactly — see "Discrepancies" below for the one place this mattered):

- `.github/workflows/deploy.yml`, `run-pipeline.yml`
- `infrastructure/terraform/*.tf` (`step_functions.tf`, `scheduler.tf`, `glue.tf`, `athena.tf`, `locals.tf`, `variables.tf`, `network.tf`, `ecs.tf`, `ecr.tf`, `s3.tf`, `iam.tf`)
- `infrastructure/terraform/templates/bootstrap_reference.asl.json.tpl`, `historical.asl.json.tpl`, `update.asl.json.tpl` (no `gold_standard.asl.json.tpl` — see below)
- `local/metabase/docker-compose.yml`
- `docs/pipelines/*.md`, `docs/aws/*.md`
- `main.py` (`VALID_SOURCES`, `FAMILY_STAGES`, `_execute_stage`, manifest assembly, `check-manifest-has-output`)
- `common/manifest.py`, `common/change_tracking.py`, `common/staged_write.py`, `common/reporting_window.py`, `common/bootstrap.py`
- `ingestion/eea/measurements.py`, `ingestion/eea/stations.py`, `ingestion/eea/nuts_boundaries.py`, `ingestion/ted/notices.py`, `ingestion/ted/codelists.py`
- `normalization/eea/stations.py`, `transformation/eea/stations.py`

## Implementation facts verified from origin/main (commit `cf48028`, "Remove GoldStandardStateMachine (#27)")

- **No Gold Layer state machine exists.** `step_functions.tf` defines exactly three `aws_sfn_state_machine` resources: `BootstrapReferenceStateMachine`, `HistoricalStateMachine`, `UpdateStateMachine`. `GoldStandardStateMachine` was removed from `main` in PR #27, merged before this deck was built.
- Gold runs as the final automatic step inside each of the three source branches of `HistoricalStateMachine`/`UpdateStateMachine`, gated by `CheckHasNewData`/`check-manifest-has-output`, always via `--discover` (full rebuild of that source's Gold table) — confirmed in the ASL `Command.$` strings for `EeaRunGold`/`TedRunGold`/`EurostatRunGold`.
- Gold currently writes **one combined Parquet file per source** (`data/gold/eea/measurements.parquet`, `data/gold/ted/notices.parquet`, `data/gold/eurostat/agriculture_accounts.parquet`) — not a per-partition file scheme (a separate, still-open PR in this repository proposes that change, but it is not merged, so this deck describes the current `main` behavior only).
- `FAMILY_STAGES` in `main.py`: `eea-measurements`/`ted-notices` → `["ingestion", "normalization", "transformation"]`; `eurostat-agriculture-accounts` → `["ingestion", "normalization"]` (no transformation stage) — exactly as shown on slides 4, 8, 9, 10.
- `VALID_SOURCES`: `eea-nuts-boundaries`, `eea-stations`, `eea-measurements`, `ted-codelists`, `ted-notices`, `eurostat-agriculture-accounts`.
- `run_id` is a UUID (`States.UUID()` in ASL / `uuid.uuid4()` in `main.py`), not a timestamp-based string.
- Stage manifest schema (`_execute_stage`, `StageResult.to_dict()`): `written_paths`, `changed_paths`, `unchanged_paths`, `failed_paths`, `status`, plus `run_id`, `source`, `stage`, `mode`, `countries`, `period`, `input_paths`, `started_at`, `finished_at` — the deck's manifest sample (slide 6) shows a representative subset of the real field names, not an invented shape.
- Glue: one `aws_glue_catalog_database` (`procurement_gold` by default) + three `aws_glue_catalog_table` resources defined explicitly in `glue.tf`, each `location` pointing at `s3://<bucket>/data/gold/<source>/` (a folder, not a single file) — **not** a Glue Crawler. Exact Gold column names/types for all three tables were read directly from `glue.tf` and used verbatim on slides 10/13 (no invented columns — e.g. TED's Gold table has **no** CPV column, corrected from an early draft).
- Athena: one `aws_athena_workgroup` (`<project>-gold`), results at `s3://<bucket>/athena-results/`.
- EventBridge Scheduler **exists** (`aws_scheduler_schedule.monthly_update`, cron `0 3 ? * MON#1 *`, Europe/Berlin) and is **created `DISABLED`** by default (`update_schedule_enabled` variable defaults to `false`) — shown as-is on slide 9.
- Local Metabase (`local/metabase/docker-compose.yml`): `http://localhost:3000`, authenticates via `AWS_PROFILE` against the operator's own local AWS CLI/SSO profile, mounted **read-only** into `/home/metabase/.aws` (not `/root/.aws` — the image's Java process runs as the unprivileged `metabase` user), no static credentials anywhere in the compose file.
- Deploy workflow: triggers on push to `main`, OIDC (no long-lived keys), `terraform fmt`/`validate`/`plan`, ensures ECR exists, builds/pushes an image tagged `sha-<12-char short commit SHA>`, applies the GitHub deploy role's own IAM policy first (documented race-avoidance), then the full `terraform apply`.
- EEA refresh window logic (`common/reporting_window.mutable_years`): current year always in scope; prior year in scope only until its own 30 September deadline — matches slide 9 exactly.
- TED update logic: per-country `state.json` with a `last_successful_run_date` cursor, queried as `publication-date >= cursor` (inclusive, deliberately overlapping), deduplicated by `publication-number`; cursor only advances after full success.
- EEA/Eurostat change detection: SHA-256 content hash via `common/change_tracking.py`, state entry shape `{"<path>": {"content_hash": "<hex>"}}`.

## Discrepancies between this specification and origin/main

1. **The specification's own Gold sample schema for TED lists a `cpv` column that does not exist in the real Gold table.** Corrected: TED's real Gold columns (from `glue.tf`) are `country_code`, `notice_publication_number`, `notice_publication_date`, `contract_conclusion_date`, `buyer_name`, `contract_total_value`, `contract_currency_code`, `place_of_performance_nuts`, `nuts1`, `nuts2`, `nuts3`, `place_of_performance_nuts_label`, `nuts1_label` — CPV is dropped at the Gold layer (it exists earlier, in Normalized/Transformed).
2. **The specification's sample manifest JSON uses a timestamp-shaped `run_id`** (`"20260826-031502-a81f"`). The real `run_id` is a UUID4 string. The deck uses a UUID-shaped example instead (`"3f2a9c1e-…-4f90ab"`).
3. **Gold currently writes one combined file per source**, not one file per precursor partition. The specification's own worked examples for slides 8/10 (e.g. `gold/eea/measurements.py --discover` → `WRITE gold/eea/measurements.parquet`) already assumed this single-file model, so no change was needed there — flagged here only because a separate, unmerged PR in this repository proposes a per-partition rewrite; this deck reflects `main` as it stands today, per the task's own instruction to prefer the repository over the spec on conflict.
4. **Request/response JSON samples on slide 3 are deliberately shortened** relative to the full real requests documented in `docs/pipelines/*.md` (e.g. TED's real query string includes `notice-type`/`classification-cpv`/`publication-date` filters; only `limit`/`latest` are shown on-slide) — this is a direct application of the task's own instruction to keep visible copy short and move detail to speaker notes, not a factual error; the full real values are described in the slide's speaker notes.
5. No other factual discrepancies were found; every other identifier, path, state name, and column name shown was read directly from the repository, not invented.

## Unresolved placeholders

- **Slide 14's two QR codes are placeholders** (a generated abstract pixel pattern, not a real scannable code) — the task's own instructions require this, since no GitHub/LinkedIn URL was available in the repository or user configuration, and inventing one was explicitly disallowed. Swap `qrPlaceholder(...)` in `slides/slide14.js` for a real QR image before external use.
- No other placeholder text, "Lorem ipsum," `TODO`, or bracketed instruction text exists anywhere in the deck (verified by the `markitdown` grep above).

## External assets and licenses

**None.** Every visual element in the deck — the field/city/station/institution/document/investment illustrations on slide 1, the flow diagrams, code blocks, tables, charts on the Metabase mockup (slide 12), and the QR placeholders (slide 14) — is drawn natively with PptxGenJS shapes, lines, and text (rectangles, ellipses, triangles, and simple compositions of them). No external images, icon libraries, fonts, or stock photography were fetched or embedded. Fonts used are Calibri (sans) and Courier New (monospace), both standard Office-installed fonts, referenced by name only (not embedded).

## Statistics

All figures on slide 13 (row/notice counts, coverage, quality, domain breakdowns) are taken verbatim from the values supplied in the task specification. No dataset exists in this sandbox (per `CLAUDE.md`'s repository-hygiene rules, downloaded raw/processed data is never committed, and none was present in the working tree), so these figures could not be independently re-derived from a live Parquet file in this session — they are presented as given, consistent with the task's own framing of them as the "current data snapshot."

## Final pass/fail checklist

| # | Check | Result |
|---|---|---|
| 1 | All 14 slides rendered to PNG | **PASS** — `rendered/slide-01.png` … `slide-14.png` |
| 2 | Every rendered slide inspected individually | **PASS** — two full passes (pre- and post-font-fix) |
| 3 | Montage / deck-level consistency reviewed | **PASS** — reviewed slide-by-slide in sequence across two full passes; palette/typography verified consistent |
| 4 | Every unintended overlap fixed | **PASS** — see "Overlap / overflow check result" above |
| 5 | Clipped text, broken arrows, unreadable JSON, awkward wrapping fixed | **PASS** — arrow-coordinate bug fixed (slides 4/5/6); all JSON blocks re-verified to fit fully in their boxes |
| 6 | Every slide title on one line unless spec allows two lines | **PASS** — only slide 1 ("Procurement & Environment" / "Data Pipeline") is two lines, as specified; all others are single-line |
| 7 | Body text ≥16pt | **PASS**, with one disclosed, deliberate exception: the page-number chip (11pt) and the small footer brand line "PROCUREMENT & ENVIRONMENT PIPELINE" (9pt) that appears on every slide are decorative page chrome, not content — analogous to a template's built-in footer/slide-number placeholder, which typically renders below body-text size in any deck. All actual content — including diagram captions, arrow-connector labels, table cells, and code blocks — is 16pt or larger. |
| 8 | EEA/TED/Eurostat colors consistent | **PASS** — `#CFE8FF`/`#FFF0B8`/`#D7F4DE` used for these three sources on every slide that shows them (3, 7, 8, 9, 10, 11, 13) |
| 9 | Slide 8 has three parallel Historical lanes | **PASS** |
| 10 | Slide 9 has three source-specific Update strategies | **PASS** |
| 11 | Gold appears inside Historical and Update | **PASS** |
| 12 | No Gold State Machine shown | **PASS** — none exists in `origin/main`; none drawn |
| 13 | Eurostat transformation not invented | **PASS** — slide 8 shows an explicit dashed "NO TRANSFORMATION STAGE" placeholder for Eurostat; slides 4, 9, 10 state this explicitly |
| 14 | Glue table creation matches the repository | **PASS** — explicit Terraform tables, not a crawler, stated on-slide and in speaker notes |
| 15 | Metabase shown as local | **PASS** |
| 16 | Statistics match supplied values exactly | **PASS** (see "Statistics" note on provenance above) |
| 17 | Exactly 14 slides | **PASS** |

**Overall: PASS**, with the two disclosed items above (footer/page-number chrome exception; QR-code placeholders) noted rather than silently resolved.
