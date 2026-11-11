# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/). Newest first.

Decisions with real trade-offs are explained in [docs/decisions/](docs/decisions/).

## [Unreleased]

### Iteration 4 - Pipeline hardening (branch `pipeline-hardening`)

#### Added
- `.github/workflows/keep_alive.yml`: runs weekly and adds an empty commit only when the newest
  commit is 30 or more days old, so GitHub does not disable the scheduled pipeline after 60 days
  without repository activity (this switched it off twice before). A manual run with `force`
  tests the push. The logic was tested locally; that an empty commit by the bot really resets
  GitHub's 60-day timer is not verified yet.

### Iteration 3 - New capture (branch `new-capture`)
The SQL migration has not been run on Supabase. The new pipeline code below writes to the new
tables, so it only works after the migration; deploy both together (roadmap decision 12).

#### Pipeline (`src/ingestion.py`)
- Fetches the whole feed (no `isActive=true`), so future planned maintenance is captured.
- Lifecycle status per item: `scheduled` (start in the future), `active`, `resolved` (left the
  feed after its start) or `cancelled` (left the feed before its start). Items with no start
  (CALAMITY) count as `active` while in the feed. Status is refreshed every run.
- Hash gate: the whole item is hashed (`HASH_VERSION = 1`, empty denylist); the parsed row and the
  raw JSON (`disruption_payloads`) are written only when the hash changes. Unchanged items only
  get `last_seen_at` and status updated.
- `ingestion_runs`: one row per run, written in its own commits so a failed run is still recorded.
- New fields parsed: topic, registration/release time, period, phase, impact, additional travel
  time (min/max).
- Removed: snapshot writes, km/haversine code, station name and coordinate parsing.
- Items with an unknown type are skipped with an error log instead of failing the run.
- Tested on a scratch Postgres with made-up items (new, unchanged, changed, time passing,
  vanished, reappearing, empty-feed guard, failed run) and with the real NS feed (105 items;
  a second run with the same data changed 0).

#### Decision records
- ADR 0002 (raw payload and hash gate) and ADR 0003 (lifecycle status) in `docs/decisions/`.

#### Dashboard (`src/app.py`)
- Reads the `disruptions_all` view (old rows + new rows, legacy columns only) instead of the
  `disruptions` table. **Do not deploy before the migration has run**, or the page breaks.

#### SQL schema files (step 1)

#### Added
- `sql/000_inspect_legacy.sql`: read-only queries to check the real legacy column types, index
  names and row level security before migrating.
- `sql/001_new_capture.sql`: one-transaction migration. Renames the old tables to `*_legacy`,
  creates `disruptions` (with lifecycle `status`), `disruption_payloads`, `ingestion_runs` and the
  `disruptions_all` union view, and locks the new tables with row level security.
- `sql/002_rollback_new_capture.sql`: restores the old names (deletes anything the new pipeline
  captured).

#### Changed
- Dashboard no longer converts `affected_km` (nothing shows it since the km KPI was removed). The
  new schema leaves out `affected_km`, `from_station`, `to_station` and their coordinates; the
  old values stay in `disruptions_legacy`.

### Iteration 2 - Dashboard fixes (branch `dashboard-improvements`, 2026-11-06)

#### Fixed
- "Active right now" no longer depends on the sidebar filters (it used to be hidden by them).
  It is queried directly and shown at the top instead of inside an expander.
- Month filter only offers months that have data in the selected year.
- Timestamps are parsed as UTC with `format="ISO8601"`. Previously a column with no values could
  crash the page, and rows whose timestamp precision differed from the first row were silently
  dropped.

#### Added
- "Data as of" line, plus a warning when the pipeline has not seen data for more than 3 hours.
- Caption listing disruption types excluded in the sidebar.

#### Changed
- Duration KPI is now the median instead of the mean, with a tooltip explaining how it is derived.
- Bar charts use one colour per chart, show values on the bars, and drop redundant axis titles and
  colourbars.

#### Still open
- Dashboard still reads all rows with `select("*")` (the 1000-row cap, see iteration 1).
- Counts only cover what the pipeline saw; periods when the scheduled workflow was disabled are
  gaps. Planned: keep-alive for the workflow and a rebuilt table.
- `consequence_level` is mostly blank or `LESS_TRAINS` in the data; the consequences chart uses
  the free-text `consequence_description` instead. Reason for the blanks not yet investigated.

### Iteration 1 - Critical bug fixes (branch `fix-critical-bugs`, 2026-11-06)

#### Fixed
- Ingestion no longer fails with `UnboundLocalError` when the NS feed is empty. The data was
  already committed, but the GitHub Actions run was marked failed.
- `last_seen_at` is no longer overwritten when a disruption is closed. It now always means
  "last run in which the disruption was in the feed". See [ADR 0001](docs/decisions/0001-last-seen-at-semantics.md).

#### Added
- Guard against a bad API response: an empty feed no longer closes all disruptions when
  `EMPTY_FEED_GUARD_THRESHOLD` (5) or more are active; the close step is skipped with a warning
  and retried on the next run. The threshold is a guess to tune from real data. Partial
  (non-empty but truncated) responses are not detected.

#### Still open from this iteration
- Dashboard reads `disruptions` with `select("*")` and is probably capped at 1000 rows by the
  Supabase API. Planned fix: aggregate in SQL views.
