-- 000_inspect_legacy.sql  (READ ONLY, safe to run any time)
--
-- Run this in the Supabase SQL editor BEFORE the migration and compare the result with
-- 001_new_capture.sql. The repo has no DDL for the existing tables, so this is how we learn
-- their real column types, index/constraint names and security settings.

-- 1. Column names and types of the two existing tables
SELECT table_name, ordinal_position, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('disruptions', 'disruption_snapshots')
ORDER BY table_name, ordinal_position;

-- 2. Indexes (names matter: renaming a table does not rename its indexes)
SELECT tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename IN ('disruptions', 'disruption_snapshots')
ORDER BY tablename, indexname;

-- 3. Constraints and owned sequences
SELECT conrelid::regclass AS table_name, conname, contype
FROM pg_constraint
WHERE conrelid IN ('public.disruptions'::regclass, 'public.disruption_snapshots'::regclass)
ORDER BY 1, 2;

-- Sequences owned by a column of either table (does not assume a column called "id")
SELECT s.relname AS sequence_name, t.relname AS table_name, a.attname AS column_name
FROM pg_class s
JOIN pg_depend d    ON d.objid = s.oid AND d.deptype IN ('a', 'i')
JOIN pg_class t     ON t.oid = d.refobjid
JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
WHERE s.relkind = 'S'
  AND t.oid IN ('public.disruptions'::regclass, 'public.disruption_snapshots'::regclass);

-- 4. Row level security and policies (what the dashboard's anon key is allowed to do)
SELECT relname, relrowsecurity AS rls_enabled, relowner::regrole AS owner
FROM pg_class
WHERE oid IN ('public.disruptions'::regclass, 'public.disruption_snapshots'::regclass);

SELECT tablename, policyname, roles, cmd, qual
FROM pg_policies
WHERE schemaname = 'public'
  AND tablename IN ('disruptions', 'disruption_snapshots');

-- 5. Row counts and what is still marked active
SELECT 'disruptions' AS t, count(*) AS rows, count(*) FILTER (WHERE is_active) AS active
FROM public.disruptions
UNION ALL
SELECT 'disruption_snapshots', count(*), NULL FROM public.disruption_snapshots;
