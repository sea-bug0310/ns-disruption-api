# Roadmap: new capture, hardened pipeline, two-tab dashboard

Written 2026-11-08. This is the working plan, so a new session (or a new reader) can pick the
project up without the chat history. Compare with [00-baseline-review.md](00-baseline-review.md),
which records where the project started. The dashboard sketch is in
[02-dashboard-wireframe.md](02-dashboard-wireframe.md).

Status legend: DONE, NEXT, PLANNED, LATER. Nothing below marked PLANNED/LATER is implemented yet.

## Goal

Learn data engineering with this project and make it a good portfolio piece. Track NS train
disruptions as a kind of performance tracking: how bad, where, when, and (for maintenance) how well
it was planned. Punctuality (Reisinformatie trips data) is deferred until after disruptions.

Working style: one branch per iteration, atomic commits, entries in `CHANGELOG.md`, an ADR in
`docs/decisions/` only for decisions with a real trade-off.

## Facts established (measured 2026-11-07, not assumptions)

- The NS Disruptions v3 feed has **no history**. Without `isActive=true` it still returns only
  current and future items; nothing has ended.
- `isActive=true`: 14 items, 73 KB, ~0.4 s. No filter: 110 items, 631 KB, ~0.3 s (the extra 96 are
  future MAINTENANCE, up to 2027-09-20). Both HTTP 200. The API allows the unfiltered call.
- The single-item endpoint `/v3/{type}/{id}` returns **404 for old ids**. Backfilling the old
  rows with stations/impact is impossible; the old rows can only be kept as they are.
- New fields (impact, phase, releaseTime, registrationTime, period, topic, expectedDuration,
  station lists, ...) are well filled for DISRUPTION and MAINTENANCE. CALAMITY has only `topic`.
- The pipeline already commits once per run ([ingestion.py:472](../../src/ingestion.py#L472)), so
  one run is already one transaction. No change needed there.
- GitHub disables scheduled workflows in a public repo after 60 days without repo activity; the
  workflow's own runs do not count. This caused two outages, so the data has gaps that cannot be
  recovered.
- Volatility probe (2026-11-08, [scripts/probe_volatility.py](../../scripts/probe_volatility.py)): the
  unfiltered feed fetched twice, 5 minutes apart. 104 items in both fetches, same ids, same
  feed order, **0 items with any field difference**. So no volatile fields and no unstable array
  order showed up in that window. It does not cover slower changes or what an edit looks like;
  see the open items.
- `disruptions-2025.csv` (rijdendetreinen.nl open data, 6,676 rows, whole of 2025): median
  duration 51 min; 6.3% under 5 min, 11.4% under 10 min, 22.5% under 20 min. Expected share of
  disruptions seen at least once: about 88.5% with 20-min polling, 96.6% with 5-min polling
  (indicative only: those records are per line segment, not the same granularity as NS items).
  The file has no NS ids, so it supports year-over-year comparison, not row-level joins.

## Decisions made

1. **Keep the old rows.** `disruptions` becomes `disruptions_legacy` and `disruption_snapshots`
   becomes `disruption_snapshots_legacy`. No new writes to them. A union view serves charts that
   only need old fields. Charts that need new fields say "since <capture date>".
2. **Drop `isActive=true`.** Needed to see future maintenance. "In the feed" no longer means
   "active", so the pipeline gets a lifecycle status (below).
3. **Store the raw JSON per item, only when it changes** (hash gate). Not the whole feed each run
   (about 45 MB/day, would fill the free tier in under two weeks).
4. **Dashboard = disruptions only, two tabs.** Unplanned (type DISRUPTION) and Planned
   maintenance (type MAINTENANCE, past and future). CALAMITY is not analysed. The "Upcoming work"
   box is removed.
5. **Coverage panel: yes.** A caption next to "Data as of" at the top, built from
   `ingestion_runs`, e.g. "coverage: 94% of expected runs; gaps: <date ranges>". It keeps the
   outage gaps visible so a pipeline gap is not read as fewer disruptions.
6. **"Active right now" is removed** (decided 2026-11-08). The dashboard is about past events.
   The top of the page keeps "Data as of", the stale warning and the coverage caption.
7. **No "Top 10 stations" chart; "corridors" becomes "lines".** Stations along a section are not
   where the incident is, so ranking them is misleading. Both tabs get "Top 10 lines", parsed from
   the `title` field (lines separated by `;`, e.g. `Amsterdam - Utrecht; Amsterdam - Gouda.`).
   Only for types DISRUPTION and MAINTENANCE: CALAMITY titles are free-text notices
   (e.g. `Engineering works: Rotterdam Central`, `Summer weather: bring water with you when you
   travel`), have no start time, and are not analysed. Checked on a real-data snippet on 2026-11-08. The API has no dedicated line field, and
   from -> to is unreliable for multi-section items (stations joined by ` | `, coordinates of
   the first only).
8. **Maintenance is treated as traveller impact too** (proposed, to confirm): capture
   `summaryAdditionalTravelTime` (min/max minutes) and show extra travel time, plus when work is
   executed (weekday / part of day from `timespans`).
9. 2025 CSV is used only for a year-to-date comparison from 2025 on (not 2011-2024).
10. **How the hash gate works** (refines decision 3). Decided 2026-11-08.
    - Hash the **whole item** in canonical form: `json.dumps(item, sort_keys=True,
      separators=(",", ":"))`, minus an explicit **denylist** of volatile fields. The denylist
      starts **empty** because the probe found no volatile fields. A denylist rather than an
      allowlist, so a field NS adds later still counts as a change.
    - **Do not sort arrays.** The probe showed no unstable order, and sorting could hide a real
      reorder. Revisit only if the monitoring below shows a problem.
    - **Store the raw payload exactly as received** in `disruption_payloads`. The canonical form
      is only used to compute the hash, so a wrong denylist loses no data.
    - Keep a `hash_version` (a column or a constant in the code). Changing the canonical rules
      makes every item hash differently once; the version explains why.
    - **Monitor `items_changed` against `items_fetched`** in `ingestion_runs`. If nearly every
      item changes on every run, the gate is broken (probably a noisy field). This ratio is the
      early warning and can feed an alert.
    - Granularity of `disruption_payloads`: one row per item **version**, not per run.
11. **Lines are a view, not a table; the fact stays at one row per disruption.** Decided
    2026-11-08.
    - `disruption_lines` is a **SQL view** over the current `title` (split on `;`, e.g. with
      `unnest(string_to_array(title, ';'))`), built in iteration 5. It feeds "Top 10 lines" by
      count and by duration. A view cannot go stale when a title changes and is cheap to change
      if the parsing changes. Revisit a physical table only if parsing gets complicated or the
      star schema needs a stable bridge.
    - `fact_disruption` has the grain **one row per disruption**. Line is many-to-many, so it
      stays out of the fact: a per-line fact would count a disruption, its duration and its km
      once per line. A disruption counts once for each line it touches, so per-line counts add
      up to more than the number of disruptions; the chart caption says so.
    - No `fact_disruption_station`, no `dim_station`, no `dim_line` (line names are only parsed
      strings, so there is nothing to describe beyond the name). Stations stay in the raw payload.
12. **Table rename and dashboard switch ship together** (decided 2026-11-09). Streamlit Cloud and
    the GitHub pipeline both read/write `disruptions` today. A compatibility view with the old
    name is not possible because the new current-state table is also called `disruptions`.
    Instead, in one short window: (1) rename the old tables to `*_legacy`, (2) create the new
    tables, (3) create one union view (e.g. `disruptions_all`, legacy columns only) over legacy
    plus new rows, (4) change the dashboard's table name to that view and push the new pipeline
    code. Expect one or two failed pipeline runs and a short dashboard error in between; that is
    accepted. Make sure `git push` works before touching Supabase, or the live pipeline would
    break with no way to deploy the fix.

## Lifecycle status

| What we see | Status |
|---|---|
| In the feed, `start` in the future | `scheduled` |
| In the feed, `start` passed | `active` |
| Gone from the feed, `start` had passed | `resolved` |
| Gone from the feed, `start` was still in the future | `cancelled` |

## Schema (first proposal, implemented in iteration 3 on 2026-11-09)

Implemented in [sql/001_new_capture.sql](../../sql/001_new_capture.sql). Differences from this first
proposal: no `disruption_timespans` table (rebuildable from the raw payloads); `disruptions` has
no km, station names or coordinates (nothing uses them; the data is in the payloads and the old
values in `disruptions_legacy`); phase is stored as `phase_id` + `phase_label`; the
`disruption_lines` view and the aggregation views are not built yet (iteration 5), only the
`disruptions_all` union view exists. The proposal below is kept as written.

- `disruptions_legacy`, `disruption_snapshots_legacy`: the existing tables, renamed.
- `disruptions`: current state per item. Adds lifecycle `status`, `impact`, `phase`,
  `release_time`, `registration_time`, `additional_travel_min_minutes`,
  `additional_travel_max_minutes`, a payload hash, and first/last seen. Maintenance timespans
  (several per item) may need a small child table to chart "when is work executed".
- `disruption_payloads`: raw jsonb per item, inserted only when the hash changes (one row per
  item version; see decision 10). This is the bronze layer. Columns to settle in iteration 3,
  likely `disruption_id`, `payload_hash`, `hash_version`, `payload`, `first_seen_at`.
- `disruption_lines` (a **view**, one row per item and line name parsed from `title`; see
  decision 11): feeds "Top 10 lines". Not a physical table.
- `disruption_stations` (child table, one row per item and station): no longer needed for the
  dashboard (no stations chart). Stations stay in the raw payload; add the table later only if a
  real station-level question appears. No `fact_disruption_station` (decided 2026-11-08: no
  planned question needs it).
- `ingestion_runs`: one row per run (started, finished, status, items fetched/changed, error).
  Feeds the coverage panel and the staleness alert.
- Views: union of legacy and new; SQL aggregation views for the dashboard (fixes the Supabase
  1000-row cap).

## Iterations

| # | Branch | Deliverable | Status |
|---|---|---|---|
| 1 | `fix-critical-bugs` | Empty-feed crash fixed, `last_seen_at` semantics (ADR 0001), empty-feed guard | DONE, merged and pushed |
| 2 | `dashboard-improvements` | Active section independent of filters, median KPI, data-as-of + stale warning, UTC parsing | DONE, merged and pushed |
| 3 | `new-capture` | Rename legacy tables, SQL schema files in `sql/`, drop `isActive=true`, lifecycle status, raw payload on change, `ingestion_runs`; ADRs 0002 + 0003; probe script and these docs committed | DONE 2026-11-09, merged and pushed; migration run in Supabase |
| 4 | `pipeline-hardening` | Keep-alive workflow, concurrency + 10-minute timeout, coverage view (`sql/003`). Staleness alert deferred to iteration 7 (Airflow): GitHub's schedule cannot be forced, so an alert on it would only show GitHub's own delays | written 2026-11-11, review and merge pending |
| 5 | `dashboard-v2` | SQL views (fix 1000-row cap, incl. the `disruption_lines` view), two tabs (no "Active right now"), hour/weekday bars, top lines, notice lead time, extra travel time, 2025 YTD comparison | IN PROGRESS: 5a to 5c deployed, 5d and 5e open, see Open items |
| 6 | `dbt-star-schema` | dbt on Postgres: staging models, star schema (`fact_disruption` at one row per disruption, `dim_cause`, `dim_date`, `dim_time`; no station or line dimension, see decision 11), tests | LATER |
| 7 | orchestration | Airflow (5-min schedule, retries) and/or a Databricks bronze/silver/gold run, to show the stack | LATER |

## Open items

- `fix-critical-bugs` and `dashboard-improvements` were merged into `main` (fast-forward) and
  pushed on 2026-11-09, after the first push failed with 403 (the stored GitHub login,
  `mi-nguyentt`, had no write access to `sea-bug0310/ns-disruption-api`; fixed by signing in
  with the right account). Streamlit Cloud should now run the iteration 2 dashboard (verify).
  If a machine gets 403 again, it is logged in as the wrong GitHub account.
- The scheduled workflow was re-enabled on GitHub (2026-11-09). The cron `7,27,47 * * * *` in
  `.github/workflows/ns_api.yml` is correct (every 20 min); GitHub delays or skips scheduled runs
  under load, so a long gap right after re-enabling is not necessarily a bug. Check the Actions
  tab; trigger "Run workflow" manually to confirm the pipeline works.
- **Iteration 3 done (2026-11-09).** `new-capture` was merged into `main` (fast-forward) and
  pushed after `sql/001_new_capture.sql` was run in Supabase (the decision 12 window; no scheduled
  run fell between the migration and the push, checked in the Actions tab). Checks: a local run
  saved 105 items (105 new), a second run changed 0, and a manual GitHub run on `main` also
  changed 0, so the hash is the same on Python 3.12 and 3.11. The live dashboard still ran the
  old code for a few minutes after the push (error at the old `app.py` line 71) and worked after
  waiting / rebooting the app. ADRs 0002 (raw payload + hash gate) and 0003 (lifecycle status)
  written.
  Choices made on the way: no `disruption_timespans` table; `disruptions_all` leaves out
  `scheduled` / `cancelled` rows and prefers the new row when an id exists in both; one-time
  close of the still-active legacy rows; RLS on the new tables with no policies (the legacy
  tables only had read policies, so access matches); `affected_km`, station names and
  coordinates not carried over.
  Finding from the real feed: for MAINTENANCE, NS sends `summaryAdditionalTravelTime` (68 of 104
  items) with only a text label ("varies per journey") and **no minutes**, so the min/max columns
  are empty for maintenance and decision 8's "extra travel time" numbers are not available there.
  The label stays in the raw payload; add a column only if a chart needs it. The real feed held
  1 DISRUPTION, 104 MAINTENANCE and no CALAMITY at that moment.
  Loose ends: `sql/000_inspect_legacy.sql` can be deleted (it did its job); the "Active right
  now" column list in `app.py` still names `affected_km`, `from_station`, `to_station` (skipped
  when absent, goes away in iteration 5); `import time` in `ingestion.py` is unused; the repo
  moved to `sea-bug0310/ns-disruption-api` (local remote updated 2026-11-11).
- Keep-alive: an empty monthly commit is the reliable method. Calling the "enable workflow" API
  may also reset the 60-day timer; unverified, test before relying on it.
- A run killed by the 10-minute timeout cannot mark itself as failed, so its `ingestion_runs` row
  stays `running`. The coverage panel and the staleness check must treat a `running` row older
  than about 15 minutes as failed, not as in progress.
- Coverage definition (2026-11-11): by gaps between successful runs, not "percent of expected runs".
  GitHub currently starts the schedule only about 4 times a day (gaps of 8+ hours seen), so a
  percentage against the 20-minute cron would read about 6%. The dashboard's stale warning
  (`STALE_AFTER_HOURS = 3` in `app.py`) is too tight for that and should be raised (e.g. to 12).
  The coverage caption itself is built in iteration 5 from the `sql/003` view `ingestion_gaps`.
  Deliberately not built yet: a runs-per-day view and a view treating runs stuck in `running` as
  failed (see the note above); add them when the staleness alert or a runs-per-day chart exists.
- **Iteration 5 plan (2026-11-11), in steps with a review between them.** 5a fix the 1000-row cap
  (done: `src/data_access.py` reads in pages; `disruptions_all` has about 1.5k rows, so the
  dashboard had been showing only 1000). 5b two tabs, no "Active right now", no type filter,
  coverage caption from `ingestion_gaps`, stale warning raised from 3 to 12 hours. 5c hour of day,
  weekday, top 10 lines by count and by duration. 5d maintenance-only charts (notice given,
  replacement service). 5e 2025 year-to-date comparison. The SQL views from the roadmap are built
  in the step that needs them (the dashboard view with duration and Amsterdam hour/weekday in 5b,
  `disruption_lines` in 5c), not up front. Views return rows (read in pages, grouped in pandas),
  not aggregates; iteration 6 (dbt) replaces them with proper tables. The maintenance tab counts
  scheduled work and leaves out cancelled work. Charts on new-only fields (impact) would hold only
  a few days of data, so they come last. No extra-travel-time chart for maintenance (NS sends no
  minutes).
- 5b (2026-11-11): `sql/004_dashboard_disruptions.sql` creates `dashboard_disruptions` (legacy
  rows as `resolved`, new rows with `status`, includes `scheduled`, leaves out `cancelled` and
  CALAMITY). Run it in Supabase BEFORE deploying the 5b dashboard code (done 2026-11-11). The coverage caption only
  has data since the run log started (2026-11-09). `disruptions_all` is now unused.
- 5c (2026-11-11): `sql/005_hour_weekday_and_lines.sql` appends `start_hour` / `start_isodow`
  (Amsterdam time) to `dashboard_disruptions` and creates `disruption_lines`. Run it in Supabase
  BEFORE deploying the 5c dashboard code (done 2026-11-11). Checked on the real feed (109 items gave 132 item-line
  rows, 10 station-only titles). Notes: the station-wide bucket is left out of the line charts and
  only counted in a caption. Directions are merged ('A - B' = 'B - A'; the line is shown in the
  sorting-first direction, so `Heerlen - Aachen Hbf` appears as `Aachen Hbf - Heerlen`). All
  dashboard dates are Amsterdam time (decided 2026-11-11, replacing the earlier UTC year/month).
  When checking totals against SQL, use the same time zone:
  `WHERE start_time AT TIME ZONE 'Europe/Amsterdam' >= '2026-01-01' AND ... < '2027-01-01'`.
- Domestic / international filter (2026-11-11, ADR 0004): `sql/006` + `scripts/load_stations.py`
  + the "Line type" sidebar filter. Deploy order: run `sql/006` in Supabase, run
  `python scripts/load_stations.py` (the flag is false everywhere until the table is filled), check
  the printed unmatched stops, run the dashboard locally, then push. In the real feed 36 of 109
  items had an international line. This is the first station-level question, so the roadmap's
  "add `disruption_stations` / `dim_station` only if a real station question appears" is
  answered for stations: `ref_station_names` is a candidate seed for iteration 6.
- For 5e: our feed uses English station names (`Amsterdam Central`) while the 2025 CSV uses Dutch
  ones (`Amsterdam Centraal`), so lines need name matching before they can be compared. The 2025
  `ns_lines` also need the same direction merge as `disruption_lines`.
- Streamlit versions differ: local `ns-api` env has 1.37.1, Streamlit Cloud installs the latest
  (Python 3.14, requirements are unpinned). `use_container_width` is deprecated in the latest but
  `width="stretch"` does not exist in 1.37.1, so the code keeps `use_container_width` until the
  local env is upgraded. Consider pinning versions in `requirements.txt`.
- 2025 comparison data: `data/disruptions-2025.csv` (CC BY 4.0, attribution in the README). Lines
  are compared via its `ns_lines` column, which matches our `title`; `rdt_lines` is Rijden de
  Treinen's own standardised list, built from their line catalogue and the affected stations (no
  published description of the matching found; they invite questions on their site).
- The README is out of date after iterations 3 and 4: it still describes the haversine km,
  `disruption_snapshots`, `is_active` and the weighted-impact KPI, and its clone URL uses the old
  repo name. Rewrite it at the end of iteration 5 (it is the portfolio front page).
- Raw-payload storage growth is an estimate (a few hundred KB/day); measure after a week.
- The volatility probe only covered 5 minutes with no real changes. Optionally repeat it with
  `--wait 60`, and look at what an edited item looks like (does an array reorder when an item is
  updated?). The `items_changed` / `items_fetched` ratio is the ongoing check.
- `consequence_level` is mostly blank or `LESS_TRAINS`; cause not investigated.
- Other small items: split requirements files, replace `use_container_width`, a clearer
  "Could not connect to Supabase" error, tests + CI (the scratch-Postgres test scripts from
  iteration 3 were not added to the repo). Commit author: set per repo to `sea-bug0310` with the
  GitHub noreply address on 2026-11-09; older commits show `MiNguyen-vn <mi@pathways.no>`.
- API specs were moved to `docs/api/` (committed 2026-11-09).
- `dim_cause` has an open question: a disruption can have several timespans with different
  causes, and `parse_timespans` joins them into one `cause_label` string (`A | B`). Check how often
  that happens before modelling it; decide between one row per cause or a primary cause.
- `dim_date` / `dim_time`: only worth it for attributes a timestamp cannot give (holidays,
  rush-hour flag). Fine as a learning choice; a small hour table is a possible middle path.

## Practical notes

- Never read `.env` (holds `NS_API_KEY`, gitignored). `scripts/probe_ns_api.py` reads it itself
  and never prints it. Run from the project root: `python scripts/probe_ns_api.py`.
- Dashboard locally: needs `.streamlit/secrets.toml` with `SUPABASE_URL` and `SUPABASE_KEY`.
- On Windows set `PYTHONIOENCODING=utf-8` when scripts print emoji.
