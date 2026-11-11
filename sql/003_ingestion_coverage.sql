-- 003_ingestion_coverage.sql
--
-- One view over `ingestion_runs` for the dashboard's coverage caption (roadmap decision 5),
-- e.g. "gaps over 12 hours: 2, longest 34 h". Additive only: no table changes, safe to run
-- while the pipeline is live. Run it by pasting the whole file into the Supabase SQL editor.
-- NOT YET RUN.
--
-- Coverage is described by GAPS between successful runs, not by "percent of expected runs":
-- GitHub currently starts the schedule only a few times a day instead of every 20 minutes, so a
-- percentage against 20 minutes would read as about 6% and mean nothing.
--
-- Not built (nothing uses them yet): a runs-per-day view, and a view that treats runs stuck in
-- 'running' as failed. Add them when the staleness alert or a runs-per-day chart exists.
--
-- Undo:  DROP VIEW ingestion_gaps;

BEGIN;

-- One row per successful run, with the time since the previous successful run. `gap` is NULL for
-- the very first run. "Gaps longer than 12 hours" is WHERE gap > interval '12 hours'. The silence
-- between the last successful run and now is not a row here; the dashboard's "Data as of" line
-- already shows it. Only timestamps are exposed (no `error` text), so the dashboard's key can
-- read it: like disruptions_all, the view runs with its owner's rights, although
-- `ingestion_runs` itself is locked by row level security.
CREATE VIEW ingestion_gaps AS
SELECT
    started_at                                              AS run_at,
    lag(started_at) OVER (ORDER BY started_at)              AS previous_run_at,
    started_at - lag(started_at) OVER (ORDER BY started_at) AS gap
FROM ingestion_runs
WHERE status = 'success';

-- (The guard keeps the script working on a plain Postgres without the Supabase roles.)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        GRANT SELECT ON ingestion_gaps TO anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        GRANT SELECT ON ingestion_gaps TO authenticated;
    END IF;
END $$;

COMMIT;
