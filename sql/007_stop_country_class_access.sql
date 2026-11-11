-- 007_stop_country_class_access.sql
--
-- Fix for 006 (iteration 5, domestic / international filter). The dashboard failed with
--   permission denied for function stop_country_class
-- because 006 was wrong about how views work: the tables inside a view are read with the view
-- owner's rights, but a function called by the view is checked against the user who queries the
-- view (the dashboard's key). 006 had also revoked EXECUTE from that user.
--
-- Two changes, both needed:
--   1. the function runs as its owner (SECURITY DEFINER), because it reads ref_station_names,
--      which row level security closes to the dashboard's key. Without this the function would
--      return NULL for every stop and every line would count as domestic. search_path is fixed so
--      the definer's rights cannot be redirected to other schemas.
--   2. the dashboard's roles may execute it.
-- Side effect: Supabase also offers the function as an API call, so the dashboard key could look
-- up the country class of a stop name. That is only the public station list; the table itself
-- stays closed.
--
-- 006 itself has been corrected too, so a fresh setup (001 ... 006, 007) ends in the same state.
-- Run this file if 006 was already run in the old form. Safe to run more than once.
-- Run in Supabase on 2026-11-11.
--
-- Undo:  ALTER FUNCTION stop_country_class(text) SECURITY INVOKER;
--        REVOKE EXECUTE ON FUNCTION stop_country_class(text) FROM anon, authenticated;
--        (that brings back the permission error: only useful before dropping the filter)

BEGIN;

ALTER FUNCTION stop_country_class(text) SECURITY DEFINER SET search_path = public, pg_temp;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        GRANT EXECUTE ON FUNCTION stop_country_class(text) TO anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        GRANT EXECUTE ON FUNCTION stop_country_class(text) TO authenticated;
    END IF;
END $$;

COMMIT;
