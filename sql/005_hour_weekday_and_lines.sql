-- 005_hour_weekday_and_lines.sql
--
-- Iteration 5, step 5c. Two changes, both additive:
--   1. dashboard_disruptions gets two columns at the end: the hour of day (0-23) and the ISO
--      weekday (1 = Monday ... 7 = Sunday) of `start_time` in Amsterdam time. CREATE OR REPLACE
--      VIEW may append columns as long as the existing ones keep their order and types.
--   2. a new view disruption_lines (roadmap decision 11): one row per item and line name, for the
--      "Top 10 lines" charts.
-- Safe to run while the pipeline is live. Run it by pasting the whole file into the Supabase SQL
-- editor, BEFORE the dashboard code that reads it is deployed. Run in Supabase on 2026-11-11.
--
-- Undo:  DROP VIEW disruption_lines;
--        then run 004_dashboard_disruptions.sql's view definition again with CREATE OR REPLACE
--        (without the two new columns), or leave the extra columns; nothing reads them but the
--        5c dashboard.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. dashboard_disruptions + start_hour, start_isodow (Amsterdam time)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW dashboard_disruptions AS
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
    n.last_seen_at,
    EXTRACT(hour FROM n.start_time AT TIME ZONE 'Europe/Amsterdam')::int   AS start_hour,
    EXTRACT(isodow FROM n.start_time AT TIME ZONE 'Europe/Amsterdam')::int AS start_isodow
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
    l.last_seen_at,
    EXTRACT(hour FROM l.start_time AT TIME ZONE 'Europe/Amsterdam')::int   AS start_hour,
    EXTRACT(isodow FROM l.start_time AT TIME ZONE 'Europe/Amsterdam')::int AS start_isodow
FROM disruptions_legacy l
WHERE l.disruption_type IN ('DISRUPTION', 'MAINTENANCE')
  AND NOT EXISTS (SELECT 1 FROM disruptions n WHERE n.id = l.id);

-- ---------------------------------------------------------------------------
-- 2. disruption_lines: one row per item and line.
--    NS puts the affected lines in `title`, separated by ';' and ending with a full stop, e.g.
--    'Alkmaar - Den Helder; Alkmaar - Hoorn.; Alkmaar - Den Helder.' (the same line can appear
--    twice). Each part is trimmed, its trailing full stop removed, and duplicates within an item
--    are dropped, so an item counts once per line.
--    A part without ' - ' is a station name, not a line (e.g. 'Rotterdam Central St.'); those go
--    into the bucket 'Station-wide or unnamed'. The dashboard leaves the bucket out of the chart
--    and mentions how many items it holds.
--    Per-line counts add up to more than the number of items: an item counts for every line it
--    touches (decision 11).
--    Direction is merged: 'Amsterdam - Utrecht' and 'Utrecht - Amsterdam' are the same line, and
--    so are 'Venlo - Mönchengladbach - Düsseldorf' and its reverse. The line is shown in
--    whichever direction sorts first (LEAST of the two strings, so the first-listed station is
--    usually the alphabetically first end), and an item that lists both directions counts once.
--    Items without a title have no row here.
--    `row_id` is unique, so the dashboard can read the view in pages (data_access.py needs a
--    unique order column; `id` alone repeats once per line).
-- ---------------------------------------------------------------------------
CREATE VIEW disruption_lines AS
SELECT
    d.id || '#' || p.line AS row_id,
    d.id,
    d.disruption_type,
    d.status,
    d.start_time,
    d.end_time,
    d.expected_duration_end_time,
    p.line
FROM dashboard_disruptions d
CROSS JOIN LATERAL (
    SELECT DISTINCT
        CASE
            WHEN cleaned LIKE '% - %' THEN LEAST(cleaned, reversed)
            ELSE 'Station-wide or unnamed'
        END AS line
    FROM (
        SELECT
            cleaned,
            -- the same stations in the opposite order
            array_to_string(
                ARRAY(
                    SELECT stop
                    FROM unnest(string_to_array(cleaned, ' - ')) WITH ORDINALITY AS s(stop, pos)
                    ORDER BY pos DESC
                ),
                ' - '
            ) AS reversed
        FROM (
            SELECT btrim(regexp_replace(btrim(part), '[. ]+$', '')) AS cleaned
            FROM unnest(string_to_array(d.title, ';')) AS part
        ) AS parts
        WHERE cleaned <> ''
    ) AS c
) AS p;

-- (The guard keeps the script working on a plain Postgres without the Supabase roles.)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        GRANT SELECT ON dashboard_disruptions, disruption_lines TO anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        GRANT SELECT ON dashboard_disruptions, disruption_lines TO authenticated;
    END IF;
END $$;

COMMIT;
