# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/). Newest first.

Decisions with real trade-offs are explained in [docs/decisions/](docs/decisions/).

## [Unreleased]

### Iteration 1 - Critical bug fixes (branch `fix-critical-bugs`, 2026-11-06)

#### Fixed
- Ingestion no longer fails with `UnboundLocalError` when the NS feed is empty. The data was
  already committed, but the GitHub Actions run was marked failed.
- `last_seen_at` is no longer overwritten when a disruption is closed. It now always means
  "last run in which the disruption was in the feed". See [ADR 0001](docs/decisions/0001-last-seen-at-semantics.md).

#### Added
- Guard against a bad API response: an empty feed no longer closes all disruptions when
  `EMPTY_FEED_GUARD_THRESHOLD` (5) or more are active; the close step is skipped with a warning
  and retried on the next run. The threshold is a guess to tune from real data. Partial
  (non-empty but truncated) responses are not detected.

#### Still open from this iteration
- Dashboard reads `disruptions` with `select("*")` and is probably capped at 1000 rows by the
  Supabase API. Planned fix: aggregate in SQL views.
