# Dashboard wireframe (two tabs)

First version drawn 2026-11-07, then revised by the decisions below. It is a text sketch, not a
mock-up. Related: [01-roadmap.md](01-roadmap.md), iteration 5 (`dashboard-v2`).

## Revisions since the first sketch (these override the sketches below)

- **"Upcoming work" box removed** from the maintenance tab. Future planned works are instead part
  of the tab's normal data: the tab covers past and future planned maintenance (this needs the
  `isActive=true` filter dropped, see roadmap).
- **"Active right now" is removed entirely** (decided 2026-11-08): the dashboard is about past
  events, not the present. The top of the page keeps only the title, filters, "Data as of" and the
  stale warning.
- **Coverage caption added** next to "Data as of", built from `ingestion_runs`.
- **"Top 10 stations" removed from both tabs.** A disruption lists every station along its
  section, so intermediate stations and big hubs would be counted for incidents that are not
  "at" them. Stations stay in the raw payload for later use.
- **"Top 10 corridors" becomes "Top 10 lines" on both tabs** (revised 2026-11-08 after looking at
  real rows). The `title` column holds the NS line names, e.g. `Amsterdam - Utrecht.` or
  `Hoofddorp - Schiphol - Hoorn; Amsterdam - Schiphol - Hoofddorp.`: lines separated by `;`, with a
  trailing period. Split on `;` and count each line once per disruption. Station-only titles
  (e.g. `Amsterdam Central St.`) go into a "station-wide" bucket. This replaces the earlier
  "routes (from -> to)" idea: for items with several sections, `from_station` / `to_station`
  hold several stations joined by ` | ` with the coordinates of only the first, so from -> to is
  unreliable.
- **Proposed for the maintenance tab, to confirm:** extra travel time for travellers
  (`summaryAdditionalTravelTime`, minimum/maximum minutes) as a KPI and a chart, and a "when is
  work executed" chart (weekday / part of day, using the item's `timespans`). Reason: maintenance
  is often done in daytime or at weekends, so for travellers it behaves like a disruption.
- **`[future]` tag** is resolved by the new capture (unfiltered feed); `[new col]` / `[new table]`
  boxes depend on iteration 3 and say "since <capture date>" because old rows lack those fields.
- **Maintenance tab has no causes chart** (every maintenance row has the cause "engineering works").
- **CALAMITY is not analysed.** Notices are not shown anywhere.
- **Boxes whose data does not exist yet are left out, not shown empty.**
- The sidebar type filter is gone; the tabs replace it. Year and month apply to both tabs.
- Replacement-service box is derived from consequence text, so it is only as good as NS's wording.

## Sketches (first version)

The tags show where the data comes from:

- `[have]`: already stored, so I could build it now
- `[new col]`: needs a new column captured by the pipeline
- `[new table]`: needs a new table
- `[future]`: needs future maintenance, which means dropping `isActive=true`

## Shared top (always visible)

```
+============================================================================+
| NS Disruptions & Maintenance            Year [2026] Month [..]             |
| Data as of 2026-11-07 09:15 UTC         (warning shown if > 3 h old)       |
|                                                                            |
| ACTIVE RIGHT NOW  (ignores the sidebar filters)                            |
|   Disruptions: 2      Maintenance: 4      Notices: 2                       |
|   type    | title              | start | end | from -> to | cause          |
|   MAINT.  | Groningen - Leer   | ...   | ... | ...        | eng. works     |
|   NOTICE  | Engineering work.. |  -    |  -  |  -         |  -             |
|                                                                            |
|   [ UNPLANNED DISRUPTIONS ]   [ Planned maintenance ]                      |
+============================================================================+
```

## Tab 1: Unplanned disruptions

```
+============================================================================+
| (tab 1)  UNPLANNED DISRUPTIONS   (type = DISRUPTION)                       |
|                                                                            |
| +---------------------+  +---------------------+  +---------------------+  |
| | Total disruptions   |  | Median duration     |  | Median impact 0-5   |  |
| |---------------------|  |---------------------|  |---------------------|  |
| | 672       [have]    |  | 2.8 h      [have]   |  | -         [new col] |  |
| +---------------------+  +---------------------+  +---------------------+  |
|                                                                            |
| +------------------------------------------------------------------------+ |
| | TREND: disruptions per month / week  (toggle)   [have]                 | |
| |------------------------------------------------------------------------| |
| | bar chart, values on the bars                                          | |
| +------------------------------------------------------------------------+ |
|                                                                            |
| HOW BAD                                                                    |
| +----------------------------------+  +----------------------------------+ |
| | Median duration by cause  [have] |  | Impact score 0-5  [new col]      | |
| +----------------------------------+  +----------------------------------+ |
|                                                                            |
| WHERE                                                                      |
| +----------------------------------+  +----------------------------------+ |
| | Top 10 corridors  [have]         |  | Top 10 stations  [new table]     | |
| |   from_station -> to_station     |  |   every station per section      | |
| +----------------------------------+  +----------------------------------+ |
|                                                                            |
| WHEN                                                                       |
| +----------------------------------+  +----------------------------------+ |
| | Starts by hour of day  [have]    |  | Starts by weekday  [have]        | |
| |   Amsterdam time                 |  |   Mon..Sun                       | |
| +----------------------------------+  +----------------------------------+ |
|                                                                            |
| WHY                                                                        |
| +----------------------------------+  +----------------------------------+ |
| | Top 10 causes  [have]            |  | Top 10 consequences  [have]      | |
| +----------------------------------+  +----------------------------------+ |
+============================================================================+
```

## Tab 2: Planned maintenance

```
+============================================================================+
| (tab 2)  PLANNED MAINTENANCE   (type = MAINTENANCE)                        |
|                                                                            |
| +---------------------+  +---------------------+  +---------------------+  |
| | Total works         |  | Median planned dur. |  | Median notice (d)   |  |
| |---------------------|  |---------------------|  |---------------------|  |
| | ...       [have]    |  | ...       [have]    |  | -         [new col] |  |
| +---------------------+  +---------------------+  +---------------------+  |
|                                                                            |
| +------------------------------------------------------------------------+ |
| | TREND: maintenance per week   [have]                                   | |
| +------------------------------------------------------------------------+ |
|                                                                            |
| WHERE                                                                      |
| +----------------------------------+  +----------------------------------+ |
| | Top 10 corridors  [have]         |  | Top 10 stations  [new table]     | |
| +----------------------------------+  +----------------------------------+ |
|                                                                            |
| PLANNING                                                                   |
| +----------------------------------+  +----------------------------------+ |
| | Notice given  [new col]          |  | Replacement service  [have]      | |
| |   start - releaseTime, in bins   |  |   share with buses running       | |
| +----------------------------------+  +----------------------------------+ |
|                                                                            |
| UPCOMING WORK  [future]                                                    |
| +------------------------------------------------------------------------+ |
| | Calendar of planned work in the next weeks                             | |
| +------------------------------------------------------------------------+ |
+============================================================================+
```
