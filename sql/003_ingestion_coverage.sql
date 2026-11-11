-- 003_ingestion_coverage.sql
--
-- Views over `ingestion_runs` for the dashboard's coverage caption (roadmap decision 5).
-- Additive only: three new views, no table changes, safe to run while the pipeline is live.
-- Run it by pasting the whole file into the Supabase SQL editor. NOT YET RUN.
--
-- Coverage is described by GAPS and runs per day, not by "percent of expected runs": GitHub
-- currently starts the schedule only a few times a day instead of every 20 minutes, so a
-- percentage against 20 minutes would read as about 6% and mean nothing.
--
-- Undo:  DROP VIEW ingestion_coverage_daily; DROP VIEW ingestion_gaps;
--        DROP VIEW ingestion_runs_effective;

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. ingestion_runs_effective: the run log with a usable status.
--    A run still 'running' after 15 minutes was killed (the workflow times out after 10) and
--    never got to mark itself, so it counts as failed. A run that started a minute ago is
--    still 'running'.
--    `error` is left out on purpose: error text can contain connection details, and the
--    dashboard's key can read these views.
-- ---------------------------------------------------------------------------
CREATE VIEW ingestion_runs_effective AS
SELECT
    id,
    started_at,
    finished_at,
    CASE
        WHEN status = 'running' AND started_at < now() - interval '15 minutes' THEN 'failed'
        ELSE status
    END AS status,
    items_fetched,
    items_changed
FROM ingestion_runs;

-- ---------------------------------------------------------------------------
-- 2. ingestion_gaps: one row per successful run, with the time since the previous successful
--    run. `gap` is NULL for the very first run. "Gaps longer than 12 hours" is
--    WHERE gap > interval '12 hours'. The silence between the last successful run and now is
--    not a row here; the dashboard's "Data as of" line already shows it.
-- ---------------------------------------------------------------------------
CREATE VIEW ingestion_gaps AS
SELECT
    started_at                                              AS run_at,
    lag(started_at) OVER (ORDER BY started_at)              AS previous_run_at,
    started_at - lag(started_at) OVER (ORDER BY started_at) AS gap
FROM ingestion_runs_effective
WHERE status = 'success';

-- ---------------------------------------------------------------------------
-- 3. ingestion_coverage_daily: runs per UTC day, including days with no run at all (those
--    rows have 0 successful runs; they are the gaps that matter most).
-- ---------------------------------------------------------------------------
CREATE VIEW ingestion_coverage_daily AS
WITH per_day AS (
    SELECT
        (started_at AT TIME ZONE 'UTC')::date           AS day,
        count(*) FILTER (WHERE status = 'success')      AS successful_runs,
        count(*) FILTER (WHERE status = 'failed')       AS failed_runs
    FROM ingestion_runs_effective
    GROUP BY 1
)
SELECT
    d::date                          AS day,
    coalesce(p.successful_runs, 0)   AS successful_runs,
    coalesce(p.failed_runs, 0)       AS failed_runs
FROM generate_series(
        (SELECT min(day) FROM per_day),
        (now() AT TIME ZONE 'UTC')::date,
        interval '1 day'
     ) AS d
LEFT JOIN per_day p ON p.day = d::date;

-- ---------------------------------------------------------------------------
-- Access: like disruptions_all, the views run with their owner's rights, so the dashboard's
-- key can read them although `ingestion_runs` itself is locked by row level security.
-- (The guard keeps the script working on a plain Postgres without the Supabase roles.)
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        GRANT SELECT ON ingestion_runs_effective, ingestion_gaps, ingestion_coverage_daily TO anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        GRANT SELECT ON ingestion_runs_effective, ingestion_gaps, ingestion_coverage_daily TO authenticated;
    END IF;
END $$;

COMMIT;
