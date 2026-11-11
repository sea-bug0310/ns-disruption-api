# 0003. Lifecycle status for items in the feed

- Status: accepted
- Date: 2026-11-09

## Context
We stopped asking NS for `isActive=true` so that planned maintenance, up to a year ahead, is
captured (about 100 of the first 105 items were future maintenance). "In the feed" therefore no
longer means "happening now". Also, the feed keeps no history: an item that ends or is withdrawn
simply disappears, with no final version (see ADR 0001).

The old boolean `is_active` (true while the item was in the feed) cannot say "planned but not
started yet" or "disappeared before it ever started".

## Options considered
1. **Keep a boolean, using NS's own `isActive` field.** Separates active from future, but cannot
   separate resolved from cancelled, and once an item leaves the feed we no longer receive its
   value, so we would have to flip it ourselves anyway.
2. **No stored state: derive it in queries** from `start_time`, `end_time` and `last_seen_at`.
   Nothing to maintain, but every chart repeats the logic, and `end_time` can be a plan rather
   than the actual end (ADR 0001).
3. **A stored `status` column with four values, maintained by the pipeline** (chosen).

## Decision
`status` is one of `scheduled`, `active`, `resolved`, `cancelled`.

| What we see | Status |
|---|---|
| In the feed, `start` in the future | `scheduled` |
| In the feed, `start` passed (or no start at all) | `active` |
| Gone from the feed, `start` had passed | `resolved` |
| Gone from the feed, `start` was still in the future | `cancelled` |

- The status of every item in the feed is recomputed on every run, including items whose JSON did
  not change: time passing turns `scheduled` into `active` without NS changing anything.
- An item that leaves the feed is judged by its `start_time` compared with the time of the run
  that noticed it is gone.
- An item that comes back is set to `scheduled` or `active` again, so a mistaken close heals
  itself (ADR 0001).
- Items with no start time (CALAMITY) count as `active` while they are in the feed.
- Empty-feed guard: if the feed returns nothing while 5 or more items are open, nothing is
  closed, because that is more likely a bad response than "everything ended at once".

## Consequences
- Charts filter on one column. The `disruptions_all` view maps `is_active` to
  `status = 'active'` and leaves out `scheduled` and `cancelled`, so the old dashboard shows what
  it showed before.
- When something ended is only known to within one run interval (about 20 minutes), using
  `last_seen_at`. After a pipeline outage an item that ended during the gap is closed at the next
  run.
- `resolved` does not tell apart an item that finished normally, finished early, or was withdrawn
  after its start. `cancelled` is an inference (it vanished before its start); an item that was
  rescheduled under a new id would look the same. Not checked.
- The close rules rely on the pipeline seeing the feed regularly. Gaps in runs (see
  `ingestion_runs`) reduce how precise the statuses are.
