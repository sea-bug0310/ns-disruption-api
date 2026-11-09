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

## Proposed schema (not implemented, to be refined in iteration 3)

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
| 3 | `new-capture` | Rename legacy tables, SQL schema files in `sql/`, drop `isActive=true`, lifecycle status, raw payload on change, `ingestion_runs`; ADR for lifecycle + raw payload; commit `scripts/probe_ns_api.py`; add these docs | NEXT |
| 4 | `pipeline-hardening` | Keep-alive workflow, staleness alert workflow, concurrency + timeout, coverage panel data | PLANNED |
| 5 | `dashboard-v2` | SQL views (fix 1000-row cap, incl. the `disruption_lines` view), two tabs (no "Active right now"), hour/weekday bars, top lines, notice lead time, extra travel time, 2025 YTD comparison | PLANNED |
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
- **Iteration 3 is to be done step by step, with a review between steps** (owner's request).
  Step 1: write the SQL schema files in `sql/` (no database changes yet). Later steps, in order:
  run the migration (decision 12), new pipeline code (drop `isActive=true`, lifecycle status,
  payload hash gate, `ingestion_runs`), dashboard switch to the union view, ADR.
  **Step 1 written 2026-11-09, awaiting review** (`sql/000`-`002`, branch `new-capture`, tested on
  a scratch Postgres with the real legacy table definitions copied from Supabase, not on Supabase
  itself). Legacy RLS checked 2026-11-09: read-only SELECT policies (`disruptions` for `public`,
  snapshots for `authenticated`), no write policies, so the new tables' "RLS on, no policies" plus
  read through the view matches the existing access). Choices to confirm: no
  `disruption_timespans` table (rebuildable from the raw payloads; decision 8 is still "proposed"),
  `disruptions_all` leaves out `scheduled`/`cancelled` rows and prefers the new row when an id
  exists in both, one-time close of still-active legacy rows, RLS on the new tables.
  **New pipeline code written 2026-11-09, awaiting review** (`src/ingestion.py`; tested on a
  scratch Postgres, including a run with the real feed, never against Supabase). Finding from the
  real feed that same day: for MAINTENANCE, NS sends `summaryAdditionalTravelTime` (68 of 104
  items) with only a text label ("varies per journey") and **no minutes**, so the min/max columns
  are empty for maintenance and decision 8's "extra travel time" numbers are not available there.
  The label stays in the raw payload; add a column only if a chart needs it. The real feed held
  1 DISRUPTION, 104 MAINTENANCE and no CALAMITY at that moment.
- Keep-alive: an empty monthly commit is the reliable method. Calling the "enable workflow" API
  may also reset the 60-day timer; unverified, test before relying on it.
- Raw-payload storage growth is an estimate (a few hundred KB/day); measure after a week.
- The volatility probe only covered 5 minutes with no real changes. Optionally repeat it with
  `--wait 60`, and look at what an edited item looks like (does an array reorder when an item is
  updated?). The `items_changed` / `items_fetched` ratio is the ongoing check.
- `consequence_level` is mostly blank or `LESS_TRAINS`; cause not investigated.
- Other small items: split requirements files, replace `use_container_width`, a clearer
  "Could not connect to Supabase" error, tests + CI, commit author config (local git identity
  differs from the GitHub account).
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
