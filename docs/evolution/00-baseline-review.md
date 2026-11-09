# Baseline review (before iteration 1)

Written 2026-11-06, before any change in this repo. It is the starting point for the project's
evolution: each later stage (new capture, dbt, orchestration, ...) can be compared against it.

**How to use it:** the review below is kept verbatim. The status table underneath is updated as
work lands, so you can see which findings are closed and which remain.

## Starting state, in short

- GitHub Actions cron (every ~20 min) runs `src/ingestion.py`: one call to the NS Disruptions API
  with `isActive=true`, parse, upsert into Supabase Postgres.
- Two tables: `disruptions` (current state, one row per disruption) and `disruption_snapshots`
  (one row per active disruption per run).
- Streamlit dashboard (`src/app.py`) reads `disruptions` with `select("*")` and computes durations
  and week/month features in pandas.
- No schema files, no tests, no raw payload stored, no run log.

## Review (verbatim)

I read all of it: `ingestion.py`, `app.py`, the workflow, the README and the git history. The foundation is solid. You have retries, a transaction with rollback, upserts, a current-state table plus a history table, and scheduled runs. Below are the problems I found, in order of how much I'd fix them first. I haven't run anything against your database, so a few points depend on how your tables are set up.

## 1. Real bugs to fix first

- **Crash on an empty feed.** [ingestion.py:457](src/ingestion.py#L457) logs `disruption["id"]` after the loop. If the API returns no active disruptions, `disruption` was never set and the run fails with `UnboundLocalError`. This happens after `commit()`, so the data is saved but the job is marked failed.
- **A bad API response can close every incident.** If the API returns an empty or truncated list, `mark_resolved_disruptions` ([line 406](src/ingestion.py#L406)) sets everything to `is_active = FALSE`. Add a sanity check, for example "don't mass-close if the count drops by more than 80%".
- **The dashboard probably truncates at 1000 rows.** [app.py:27](src/app.py#L27) does `select("*")`, and Supabase's API returns at most 1000 rows by default. Your history will be silently cut off once you pass that. Either paginate or have the database do the aggregation.
- **`last_seen_at` is overwritten when an incident closes** ([line 410](src/ingestion.py#L410)). It then means "closed at", not "last seen". Add a separate `resolved_at` column.

## 2. Data engineering gaps that matter most for your growth

1. **Keep the raw data.** You parse the JSON and throw it away. If you later change the parsing, such as the km calculation, you can't reprocess history. Store the raw payload as `jsonb` in a raw table, then derive clean tables from it. This is the bronze/silver/gold idea.
2. **Stop snapshotting unchanged rows.** `insert_snapshot` writes every active disruption on every run, about 72 times a day each. Compare a hash of the row and insert only when it changed. That makes it a proper slowly-changing-dimension (SCD2) history. It also keeps you inside Supabase's free-tier size limit.
3. **Model the data properly.** `join_unique` squashes multiple causes and stations into strings like `"A | B"`. Your "Top 10 Causes" chart then counts the combined string as one category. Instead, use child tables, for example `disruption_sections` and `disruption_causes`, plus a station dimension. This is the biggest data-modelling lesson in the project.
4. **Put the schema in the repo.** There is no DDL in the repo, so the tables were presumably made by hand. Add `sql/` migrations (plain SQL files or Alembic) with primary keys, indexes, and a unique key on `(disruption_id, fetched_at)`.
5. **Use your history table.** Nothing reads `disruption_snapshots`. Analyses like "how often does the expected end time slip?" or "which causes run longer than predicted?" would use the data you're already collecting.
6. **Move business logic out of Streamlit.** Duration and week/month logic live in `load_data()`. Put them in SQL views (or dbt) so the dashboard only reads finished tables.

## 3. Reliability and operations

- **Run log table.** Write one row per run to an `ingestion_runs` table with rows fetched, rows changed, duration and status. The `ingestion.log` file is useless in GitHub Actions because the runner is discarded.
- **Data-quality checks.** Look for null ids, end before start, impossible coordinates, and no new data for a long time. You can write them as plain assertions or use dbt tests or Great Expectations.
- **Workflow hardening** in [ns_api.yml](.github/workflows/ns_api.yml). Add a `concurrency` group so runs can't overlap, a `timeout-minutes`, and pip caching. Also know that GitHub cron can run late or skip runs. Scheduled workflows in public repos are disabled after 60 days without repo activity, so your pipeline could quietly stop.
- **Split requirements.** The ingestion job installs streamlit, plotly and pandas every 20 minutes. Make `requirements-ingest.txt` and `requirements-app.txt`. You unpinned versions to speed up Streamlit Cloud, so also consider a lockfile (for example `uv`) for reproducible builds.

## 4. Code quality

- **Tests.** `haversine_km`, `calculate_section_km` and `parse_disruption` are pure functions, so they're easy to test. Save a sample API response as a fixture and add `pytest` plus a CI job on pull requests.
- **Batch the writes.** Use `psycopg2.extras.execute_values` instead of two `execute` calls per row. Fetch from the API before opening the DB connection. Use context managers for the connection and cursor.
- **Remove duplication.** The 20-column insert lists appear twice, and the four `except` blocks do almost the same thing.
- **Avoid import side effects.** `basicConfig` and `load_dotenv()` run at import time, which makes the module awkward to test.
- **Precision.** `round(total_km, 0)` drops precision you can't get back. Store 2 decimals and round only in the display.

## 5. Small things

- The README's clone URL points to `ns-disruption-dashboard`, but the repo is `ns-disruption-api`.
- The README still lists "cumulative weighted impact", which you removed in your last commit.
- Check that the Supabase anon key used by the dashboard is protected by Row Level Security with read-only policies.

## What I'd do, in order

1. Fix the four bugs in section 1. This takes about an hour.
2. Add the schema file, the raw jsonb table, and the `ingestion_runs` table.
3. Split causes and sections into child tables and snapshot only on change.
4. Add tests and CI.
5. Build a dashboard page on the snapshot history.

I can do any of these directly in your repo. I'd start with the bug fixes and the tests, since those are small and let you check my changes quickly.


## Status

Updated as work lands. "Open" means not addressed yet.

| Finding | Status | Where |
|---|---|---|
| 1. Crash on empty feed | Fixed | Iteration 1 |
| 1. Bad API response closes every incident | Partly: empty feed is guarded (threshold 5); truncated feeds are not detected | Iteration 1 |
| 1. Dashboard truncates at 1000 rows | Open | Planned: SQL views |
| 1. `last_seen_at` overwritten on close | Fixed (kept `last_seen_at`, no `resolved_at` needed so far) | Iteration 1, ADR 0001 |
| 2.1 Keep the raw data | Open | Planned: new capture |
| 2.2 Snapshot only on change | Open | Planned: new capture |
| 2.3 Model causes/sections in child tables | Open | Planned |
| 2.4 Schema in the repo | Open | Planned |
| 2.5 Use the history table | Open | Planned |
| 2.6 Business logic out of Streamlit | Open | Planned: SQL views / dbt |
| 3. Run log table | Open | Planned: `ingestion_runs` |
| 3. Data-quality checks | Open | |
| 3. Workflow hardening (concurrency, timeout, keep-alive) | Open | Planned |
| 3. Split requirements | Open | |
| 4. Tests / CI | Open | |
| 4. Batch writes, remove duplication, import side effects | Open | |
| 4. Store km with 2 decimals | Open | |
| 5. README fixes, RLS check | Open | |
