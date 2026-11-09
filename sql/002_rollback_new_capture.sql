-- 002_rollback_new_capture.sql
--
-- Undoes 001_new_capture.sql: drops the new objects and restores the old names, so the OLD
-- pipeline and dashboard code work again.
--
-- WARNING: this DELETES everything captured by the new pipeline (disruptions, payloads,
-- ingestion_runs). It cannot be undone. The one-time "close all active legacy rows" step of
-- 001 is not reversed either (the old pipeline re-opens rows that are still in the feed).

BEGIN;

DROP VIEW  IF EXISTS disruptions_all;
DROP TABLE IF EXISTS disruption_payloads;
DROP TABLE IF EXISTS ingestion_runs;
DROP TABLE IF EXISTS disruptions;

ALTER TABLE disruptions_legacy          RENAME TO disruptions;
ALTER TABLE disruption_snapshots_legacy RENAME TO disruption_snapshots;

ALTER INDEX IF EXISTS disruptions_legacy_pkey RENAME TO disruptions_pkey;

COMMIT;
