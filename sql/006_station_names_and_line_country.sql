-- 006_station_names_and_line_country.sql
--
-- Iteration 5, domestic / international filter. Needs a list of station names with their country,
-- because a line title only holds city names ('Amsterdam - Brussel - Paris').
--   1. table ref_station_names (name, country): filled by scripts/load_stations.py from the NS
--      stations API (all names, short names and synonyms of every station, with its country).
--   2. function stop_country_class(stop): 'NL', 'foreign' or NULL (unknown) for one stop.
--   3. disruption_lines gets `is_international` at the end: true when at least one stop of the
--      line is foreign.
-- Additive: one table, one function, one view definition replaced with an extra column. Safe to
-- run while the pipeline is live. Run it by pasting the whole file into the Supabase SQL editor,
-- then run `python scripts/load_stations.py` (the flag is false for every line until the table is
-- filled), BEFORE the dashboard code that reads the flag is deployed. Run in Supabase on
-- 2026-11-11. NOTE: the first version of this file revoked EXECUTE on the function and broke
-- the dashboard (see 007); this file now has the corrected function, 007 repairs a database
-- where the first version ran.
--
-- How a stop is classified (first rule that applies):
--   1. its name equals a Dutch station name                      -> 'NL'
--   2. its name equals any other station name                    -> 'foreign'
--   3. a Dutch station name starts with it ('Amsterdam' -> 'Amsterdam Centraal') -> 'NL'
--   4. another station name starts with it ('Berlin' -> 'Berlin Hbf') -> 'foreign'
--   5. no match                                                  -> NULL, counted as domestic
-- "Starts with" means the stop followed by a space or a hyphen, so 'Leer' matches 'Leer
-- (Ostfriesl)' but not 'Leerdam'. For 'Liège / Luik' only the part before ' / ' is used.
-- scripts/load_stations.py prints the stops that match nothing, so wrong guesses can be spotted.
--
-- Undo:  DROP VIEW disruption_lines; then run the disruption_lines part of
--        005_hour_weekday_and_lines.sql again; DROP FUNCTION stop_country_class(text);
--        DROP TABLE ref_station_names;

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Station names with their country (NS country codes: NL, D, B, F, GB).
--    Row level security on with no policy: only the pipeline user and the views read it.
-- ---------------------------------------------------------------------------
CREATE TABLE ref_station_names (
    name    text NOT NULL,
    country text NOT NULL,
    PRIMARY KEY (name, country)
);

ALTER TABLE ref_station_names ENABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- 2. stop_country_class: see the rules in the header.
-- ---------------------------------------------------------------------------
CREATE FUNCTION stop_country_class(stop text) RETURNS text
LANGUAGE sql STABLE
SECURITY DEFINER SET search_path = public, pg_temp   -- see the comment below and 007
AS $$
    WITH s AS (SELECT btrim(split_part(stop, ' / ', 1)) AS name)
    SELECT CASE
        WHEN EXISTS (SELECT 1 FROM ref_station_names r, s
                     WHERE r.name = s.name AND r.country = 'NL') THEN 'NL'
        WHEN EXISTS (SELECT 1 FROM ref_station_names r, s
                     WHERE r.name = s.name) THEN 'foreign'
        WHEN EXISTS (SELECT 1 FROM ref_station_names r, s
                     WHERE r.country = 'NL'
                       AND (starts_with(r.name, s.name || ' ') OR starts_with(r.name, s.name || '-'))) THEN 'NL'
        WHEN EXISTS (SELECT 1 FROM ref_station_names r, s
                     WHERE r.country <> 'NL'
                       AND (starts_with(r.name, s.name || ' ') OR starts_with(r.name, s.name || '-'))) THEN 'foreign'
        ELSE NULL
    END
    FROM s
$$;

-- A view reads its tables with the view owner's rights, but a function it calls is checked against
-- the user who queries the view (the dashboard's key). So that key must be allowed to execute
-- the function, and the function runs as its owner (SECURITY DEFINER above) because it reads
-- ref_station_names, which row level security closes to that key. Side effect: Supabase also
-- offers the function as an API call; it only returns the country class of a stop name from
-- the public station list, and the table itself stays closed.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        GRANT EXECUTE ON FUNCTION stop_country_class(text) TO anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        GRANT EXECUTE ON FUNCTION stop_country_class(text) TO authenticated;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 3. disruption_lines + is_international (one row per item and line, see 005 for the rest).
--    The flag is computed per line from its stops. The 'Station-wide or unnamed' bucket can
--    collect several station-only parts of one item, so rows are grouped per line and the flag
--    is true if any of those parts is foreign (this keeps row_id unique).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW disruption_lines AS
SELECT
    d.id || '#' || p.line AS row_id,
    d.id,
    d.disruption_type,
    d.status,
    d.start_time,
    d.end_time,
    d.expected_duration_end_time,
    p.line,
    p.is_international
FROM dashboard_disruptions d
CROSS JOIN LATERAL (
    SELECT line, bool_or(has_foreign_stop) AS is_international
    FROM (
        SELECT
            CASE
                WHEN cleaned LIKE '% - %' THEN LEAST(cleaned, reversed)
                ELSE 'Station-wide or unnamed'
            END AS line,
            EXISTS (
                SELECT 1
                FROM unnest(string_to_array(cleaned, ' - ')) AS stop
                WHERE stop_country_class(stop) = 'foreign'
            ) AS has_foreign_stop
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
    ) AS x
    GROUP BY line
) AS p;

-- (The guard keeps the script working on a plain Postgres without the Supabase roles.)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        GRANT SELECT ON disruption_lines TO anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        GRANT SELECT ON disruption_lines TO authenticated;
    END IF;
END $$;

COMMIT;
