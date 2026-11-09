# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/). Newest first.

Decisions with real trade-offs are explained in [docs/decisions/](docs/decisions/).

## [Unreleased]

### Iteration 3 - New capture (branch `new-capture`), step 1: SQL schema files
Nothing in the database or the pipeline has changed yet; these files are not run.

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
