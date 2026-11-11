-- 004_dashboard_disruptions.sql
--
-- The view the dashboard v2 reads (iteration 5, step 5b). One row per unplanned disruption or
-- planned maintenance item: old rows (disruptions_legacy) plus rows from the new pipeline.
-- Additive only: one new view, no table changes, safe to run while the pipeline is live.
-- Run it by pasting the whole file into the Supabase SQL editor, BEFORE the dashboard code that
-- reads it is deployed. NOT YET RUN.
--
-- Differences from disruptions_all (which the old dashboard reads and which stays untouched):
--   - it has `status` (scheduled / active / resolved) instead of `is_active`;
--   - it includes `scheduled` rows, i.e. planned maintenance that has not started yet, because
--     the maintenance tab covers past and future work;
--   - it leaves out `cancelled` rows and CALAMITY notices (not analysed, roadmap decision 4);
--   - it only has the columns the dashboard uses. Add columns when a chart needs them
--     (CREATE OR REPLACE VIEW may append columns at the end).
--
-- Legacy rows get status 'resolved': 001 closed every still-active legacy row, and those rows
-- only ever existed because they were active at some point.
-- A legacy row whose id also exists in the new table is dropped (the new row wins) and takes the
-- earlier first_seen_at, as in disruptions_all.
--
-- Undo:  DROP VIEW dashboard_disruptions;

BEGIN;

CREATE VIEW dashboard_disruptions AS
SELECT
    n.id,
    n.disruption_type,
    n.status,
    n.title,
    n.start_time,
    n.end_time,
    n.expected_duration_end_time,
    n.consequence_description,
    n.consequence_level,
    n.cause_label,
    LEAST(n.first_seen_at, l.first_seen_at) AS first_seen_at,
    n.last_seen_at
FROM disruptions n
LEFT JOIN disruptions_legacy l ON l.id = n.id
WHERE n.status IN ('scheduled', 'active', 'resolved')
  AND n.disruption_type IN ('DISRUPTION', 'MAINTENANCE')
UNION ALL
SELECT
    l.id,
    l.disruption_type,
    'resolved' AS status,
    l.title,
    l.start_time,
    l.end_time,
    l.expected_duration_end_time,
    l.consequence_description,
    l.consequence_level,
    l.cause_label,
    l.first_seen_at,
    l.last_seen_at
FROM disruptions_legacy l
WHERE l.disruption_type IN ('DISRUPTION', 'MAINTENANCE')
  AND NOT EXISTS (SELECT 1 FROM disruptions n WHERE n.id = l.id);

-- (The guard keeps the script working on a plain Postgres without the Supabase roles.)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        GRANT SELECT ON dashboard_disruptions TO anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        GRANT SELECT ON dashboard_disruptions TO authenticated;
    END IF;
END $$;

COMMIT;
