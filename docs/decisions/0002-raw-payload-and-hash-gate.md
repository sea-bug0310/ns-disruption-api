# 0002. Store the raw item when it changes (hash gate)

- Status: accepted
- Date: 2026-11-09

## Context
The old pipeline parsed each NS item into columns and threw the JSON away. When a parsing rule
turned out to be wrong (the km calculation), or a field we skipped turned out to matter, the
history could not be reprocessed. The NS feed keeps no history: it only returns items that are
current or in the future, and the single-item endpoint answers 404 for old ids. Whatever we did
not keep at the time is lost.

The pipeline runs every 20 minutes (72 runs a day) and most runs see the same items as the run
before.

## Options considered
1. **Parse only, as before.** Smallest, but nothing can be reprocessed.
2. **Store the whole feed on every run.** Complete and simple. The unfiltered feed is about
   631 KB, so about 45 MB a day, which would fill the free database tier in under two weeks, and
   almost all of it would be duplicates.
3. **Store an item's raw JSON only when it changed** (chosen). Storage grows with the number of
   changes instead of the number of runs. It needs a cheap way to tell "changed": a hash per
   item.

How to compute that hash:
- *Hash chosen fields only (allowlist).* A change in a field we did not list goes unnoticed,
  and those are the fields we want to keep.
- *Hash the whole item minus a list of noisy fields (denylist)* (chosen). A field NS adds later
  counts as a change automatically. The risk is a noisy field causing false changes.
- *Sort arrays before hashing?* Not sorted: sorting could hide a real reorder, and the probe
  found no unstable order.

## Decision
Option 3, with a whole-item hash.
- The hash is SHA-256 of the item as JSON with sorted keys and no whitespace, minus
  `VOLATILE_FIELDS` in `src/ingestion.py`. That list starts empty: the volatility probe fetched
  the unfiltered feed twice, 5 minutes apart (104 items), and found no field differences.
- The raw item is stored as `jsonb` in `disruption_payloads`, one row per item **version**, not
  per run, together with the hash and `HASH_VERSION`. `jsonb` keeps the content and the array
  order but may reorder object keys, so "as received" means the same content, not the same
  bytes. The hash is computed from the item before it is stored.
- `HASH_VERSION` records which hashing rules were used. Changing the rules makes every item hash
  differently once, so every item gets one new payload row; the version explains why.
- When the hash changes, the parsed row in `disruptions` and the payload are written in the same
  transaction. An unchanged item only gets `last_seen_at` and `status` updated.

## Consequences
- Any field can be reprocessed later. The km and station columns were dropped from the table but
  their source data is still in the payloads. The payloads are also the raw layer for the
  planned dbt models.
- First real run on 2026-11-09: 105 items saved, 105 changed; second run: 0 changed. The same
  hash came out on the laptop (Python 3.12) and on GitHub's runner (3.11).
- Storage so far: 105 items take about 344 kB. Growth is estimated at a few hundred KB a day;
  measure after a week. No retention rule yet.
- **Watch `items_changed` against `items_fetched` in `ingestion_runs`.** If nearly every item
  changes on every run, the gate is broken, most likely by a noisy field. That ratio is the early
  warning.
- The probe covered only 5 minutes with no real edits. Whether an edited item reorders its
  arrays is not checked.
- A change that is reverted within one 20-minute gap is never seen.
- Rows captured before 2026-11-09 have no payload and cannot be backfilled.
