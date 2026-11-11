# 0004. Telling domestic from international lines

- Status: accepted
- Date: 2026-11-11

## Context
In the "Top 10 lines" charts many lines run between the Netherlands and another country, mostly
Germany, and you want to look at domestic or international lines on their own. The only
line information we have is the item `title`, e.g. `Amsterdam - Brussel - Paris.`, which lists
city names. There is no country in it.

## Options considered
1. **A short hand-written list of foreign places** in a SQL table. Quick, but it silently misses
   every foreign place nobody added, and nothing tells us when that happens.
2. **The country codes in the raw payloads.** Every station in `publicationSections` carries a
   `countryCode`. But payloads only exist since 2026-11-09 (the older rows have none), an item
   mixes several lines, and the stations there use station names (`Berlin Hbf`) where titles use
   city names (`Berlin`): only 45 of 72 title stops matched a station name exactly.
3. **A reference table of station names and countries from the NS stations API** (chosen), plus a
   rule to match city names. The API lists 737 stations (NL, D, B, F, GB) with long, medium and
   short names and synonyms; 62 of the 72 title stops matched by name or synonym.

## Decision
Option 3. `ref_station_names (name, country)` is filled by `scripts/load_stations.py` and a SQL
function `stop_country_class` classifies each stop of a line (sql/006):
1. its name equals a Dutch station name: NL;
2. equals another station name: foreign;
3. a Dutch station name starts with it (`Amsterdam` for `Amsterdam Centraal`): NL;
4. another station name starts with it (`Berlin` for `Berlin Hbf`): foreign;
5. no match: unknown, counted as domestic.

"Starts with" means the stop plus a space or hyphen, so `Leer` matches `Leer (Ostfriesl)` but not
`Leerdam`. A line is international if at least one of its stops is foreign, and the flag lives in
the `disruption_lines` view. The dashboard filter has two values, Domestic and International, and
both can be selected; an item with a domestic and an international line appears under both.

## Consequences
- It works for all rows, old and new, because it only needs the title.
- The loader prints the stops that match no station, so a missed foreign place shows up in a
  list instead of silently counting as domestic. Today's only miss is `Rotterdam Central St`
  (an English station-only title), which is correctly domestic.
- Station names must be refreshed by re-running the loader if NS adds or renames stations. The
  loader refuses to replace the table with a suspiciously small answer.
- The rule is a heuristic. A foreign place whose name is also the start of a Dutch station
  (rule 3 runs before rule 4) would be counted as domestic. Not seen in the data so far.
- This is the first station-level question, so the reference table also answers the roadmap's open
  question about a station dimension for the later dbt work (iteration 6).
