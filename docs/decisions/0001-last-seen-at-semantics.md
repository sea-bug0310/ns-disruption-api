# 0001. last_seen_at semantics

- Status: accepted
- Date: 2026-11-06

## Context
The ingestion job asks NS for `isActive=true` only. A disruption that ends simply disappears from
the feed, so `mark_resolved_disruptions` closes rows that are active in the database but missing
from the current feed. That function used to set `last_seen_at` to the close time, so for resolved
rows the column meant "closed at" instead of "last seen".

## Options considered
1. **Add a `resolved_at` column** and set it on close. Explicit, but redundant: NS already supplies
   start and end times. It must also be reset to NULL when a row returns after a false close,
   otherwise an active row keeps a stale resolution time.
2. **Stop updating `last_seen_at` on close** (chosen). Only the upsert writes it.
3. Leave as is. The column has two meanings, which is confusing for later analysis.

## Decision
Option 2. `last_seen_at` always means "last run in which the disruption was in the feed". The
resolution time is approximated by `last_seen_at`, accurate to one run interval (about 20 minutes).

## Consequences
- No new column and no reset logic; a false close self-heals when the row reappears.
- NS `end_time` may be a planned rather than actual end, because the final version of a record is
  never fetched with `isActive=true`. Check resolved rows to see whether `end_time` or
  `last_seen_at` is the better end estimate.
- Rows closed before 2026-11-06 still hold the old close time in `last_seen_at`. It can be
  recomputed from `max(fetched_at)` per disruption in `disruption_snapshots`.
