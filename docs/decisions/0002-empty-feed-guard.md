# 0002. Empty-feed guard

- Status: accepted
- Date: 2026-11-06

## Context
If NS returns an empty list by mistake, `mark_resolved_disruptions` would mark every active
disruption as resolved. The next good run reactivates them, but `disruption_snapshots` would have
a one-run gap for each incident, since snapshots are only written for incidents present in the feed.

## Options considered
1. **Do nothing** and rely on the next run to reactivate rows. Leaves gaps in the snapshot history.
2. **Skip the close step when the feed is empty and at least N disruptions are active** (chosen).
3. Skip whenever the feed shrinks sharply (for example more than 80%). Catches partial responses
   too, but needs more tuning and is harder to reason about.

## Decision
Option 2 with `EMPTY_FEED_GUARD_THRESHOLD = 5`. The value is a guess and should be tuned from the
real number of simultaneously active disruptions.

## Consequences
- A genuine "everything resolved" moment is delayed by one run (about 20 minutes) when 5 or more
  were active. Below the threshold, everything is closed as before.
- The skip is logged as a warning, so it can be spotted in the GitHub Actions logs.
- Partial (non-empty but truncated) responses are still not detected. Revisit with option 3 if
  that is ever observed.
