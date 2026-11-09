-- 001_new_capture.sql
--
-- Iteration 3 migration (roadmap decisions 1, 3, 10, 11, 12). NOT YET RUN.
--
-- Everything is in ONE transaction: Postgres DDL is transactional, so if any statement fails
-- (for example a name clash) nothing changes. Before running it:
--   1. run 000_inspect_legacy.sql and check the names/types below match,
--   2. make sure `git push` works (decision 12),
--   3. run it, then deploy the new pipeline code and dashboard change straight after.
-- Until then the old pipeline (still writing `disruptions`) will fail; that is accepted.
--
-- Undo: 002_rollback_new_capture.sql.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Keep the old data: rename the tables and the objects that carry the old names.
--    Renaming a table does NOT rename its primary key index, and the new `disruptions` table
--    would clash with `disruptions_pkey`, so that one is renamed too. (IF EXISTS: the name is
--    the Postgres default; check it against 000_inspect_legacy.sql. If it differs, the CREATE
--    TABLE below fails and the whole transaction rolls back.) The snapshots table is not
--    recreated, so nothing of it needs renaming beyond the table itself.
-- ---------------------------------------------------------------------------
ALTER TABLE disruptions          RENAME TO disruptions_legacy;
ALTER TABLE disruption_snapshots RENAME TO disruption_snapshots_legacy;

ALTER INDEX IF EXISTS disruptions_pkey RENAME TO disruptions_legacy_pkey;

-- Freeze the legacy table. Nothing writes to it any more, so any row still marked active
-- would stay "active" forever. Close them once, here. last_seen_at is not touched (ADR 0001).
UPDATE disruptions_legacy SET is_active = FALSE WHERE is_active = TRUE;

-- ---------------------------------------------------------------------------
-- 2. disruptions: current state, one row per NS item.
--    Keeps every legacy column (the dashboard and the union view need them) and adds the
--    new fields. The raw item is not here, it is in disruption_payloads.
--    No legacy `is_active`: `status` replaces it (see lifecycle table in the roadmap).
--    id is the primary key as before; ids are assumed unique across item types.
-- ---------------------------------------------------------------------------
CREATE TABLE disruptions (
    id                              text PRIMARY KEY,
    disruption_type                 text NOT NULL
        CHECK (disruption_type IN ('DISRUPTION', 'MAINTENANCE', 'CALAMITY')),
    status                          text NOT NULL
        CHECK (status IN ('scheduled', 'active', 'resolved', 'cancelled')),
    local                           boolean,
    title                           text,
    topic                           text,

    start_time                      timestamptz,   -- null for CALAMITY
    end_time                        timestamptz,   -- may be null / planned, not actual (ADR 0001)
    registration_time               timestamptz,
    release_time                    timestamptz,
    period                          text,

    phase_id                        text,
    phase_label                     text,
    impact                          numeric,       -- 0 to 5, null for CALAMITY

    expected_duration_description   text,
    expected_duration_end_time      timestamptz,

    additional_travel_min_minutes   numeric,       -- summaryAdditionalTravelTime
    additional_travel_max_minutes   numeric,

    consequence_description         text,          -- legacy columns, parsed as before
    consequence_level               text,
    situation_label                 text,
    cause_label                     text,

    payload_hash                    text NOT NULL, -- hash of the latest stored payload (decision 10)
    first_seen_at                   timestamptz NOT NULL,
    last_seen_at                    timestamptz NOT NULL  -- last run the item was in the feed
);

CREATE INDEX disruptions_status_idx     ON disruptions (status);
CREATE INDEX disruptions_start_time_idx ON disruptions (start_time);

-- ---------------------------------------------------------------------------
-- 3. disruption_payloads: bronze layer. One row per item VERSION (a new row only when the
--    hash changes), not per run. Replaces disruption_snapshots, which is not recreated.
--    jsonb keeps the content exactly but may reorder object keys; array order is kept.
--    No UNIQUE (disruption_id, payload_hash): an item can legitimately go A -> B -> A.
-- ---------------------------------------------------------------------------
CREATE TABLE disruption_payloads (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    disruption_id  text        NOT NULL REFERENCES disruptions (id),
    payload_hash   text        NOT NULL,
    hash_version   smallint    NOT NULL,   -- bump when the canonical-form rules change
    payload        jsonb       NOT NULL,
    first_seen_at  timestamptz NOT NULL    -- when this version was first fetched
);

CREATE INDEX disruption_payloads_item_idx ON disruption_payloads (disruption_id, first_seen_at DESC);

-- ---------------------------------------------------------------------------
-- 4. ingestion_runs: one row per run, feeds the coverage panel and the staleness alert.
--    The pipeline must write this row in its OWN transaction (insert 'running' and commit
--    first, update at the end), otherwise a failed run rolls back its own record.
--    Watch items_changed / items_fetched: near 1.0 on every run means the hash gate is broken.
-- ---------------------------------------------------------------------------
CREATE TABLE ingestion_runs (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    status         text        NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'success', 'failed')),
    items_fetched  integer,
    items_changed  integer,
    error          text
);

CREATE INDEX ingestion_runs_started_at_idx ON ingestion_runs (started_at DESC);

-- ---------------------------------------------------------------------------
-- 5. disruptions_all: legacy columns only, old rows + new rows. This is what the dashboard
--    reads until iteration 5 replaces it with aggregation views.
--    - New rows that are still 'scheduled' or 'cancelled' are left out: the old dashboard never
--      had them and would show them as events in 2027.
--    - `affected_km`, `from_station`, `to_station` and their coordinates are not carried over:
--      nothing uses them any more (km removed in 0ccee40; the station columns only fed the
--      "Active right now" table). Old values stay in disruptions_legacy; new ones can be
--      recomputed from the raw payloads. Step 4 must delete the `affected_km` line in app.py,
--      which reads the column directly.
--    - A legacy row whose id also exists in the new table is dropped (new row wins). Those are
--      items that were live during the switch. The new row takes the earlier first_seen_at.
-- ---------------------------------------------------------------------------
CREATE VIEW disruptions_all AS
SELECT
    n.id,
    n.disruption_type,
    n.local,
    n.title,
    (n.status = 'active')                                        AS is_active,
    n.start_time,
    n.end_time,
    n.expected_duration_description,
    n.expected_duration_end_time,
    n.consequence_description,
    n.consequence_level,
    n.situation_label,
    n.cause_label,
    LEAST(n.first_seen_at, l.first_seen_at)         AS first_seen_at,
    n.last_seen_at
FROM disruptions n
LEFT JOIN disruptions_legacy l ON l.id = n.id
WHERE n.status IN ('active', 'resolved')
UNION ALL
SELECT
    l.id,
    l.disruption_type,
    l.local,
    l.title,
    l.is_active,
    l.start_time,
    l.end_time,
    l.expected_duration_description,
    l.expected_duration_end_time,
    l.consequence_description,
    l.consequence_level,
    l.situation_label,
    l.cause_label,
    l.first_seen_at,
    l.last_seen_at
FROM disruptions_legacy l
WHERE NOT EXISTS (SELECT 1 FROM disruptions n WHERE n.id = l.id);

-- ---------------------------------------------------------------------------
-- 6. Access. The pipeline connects with DATABASE_URL as the table owner, which bypasses row
--    level security. The dashboard uses the Supabase anon key, so lock the new tables (RLS on,
--    no policies = anon can read and write nothing) and let it read through the view only.
--    A view runs with its owner's rights, so it can still read the locked tables.
--    Check 000_inspect_legacy.sql (section 4) for what the legacy tables allowed.
-- ---------------------------------------------------------------------------
ALTER TABLE disruptions         ENABLE ROW LEVEL SECURITY;
ALTER TABLE disruption_payloads ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingestion_runs      ENABLE ROW LEVEL SECURITY;

-- Make sure the dashboard roles can read the view (Supabase normally grants this by default;
-- the check keeps the script working on a plain Postgres without these roles).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        GRANT SELECT ON disruptions_all TO anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        GRANT SELECT ON disruptions_all TO authenticated;
    END IF;
END $$;

COMMIT;
