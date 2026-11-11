# Data

## disruptions-2025.csv

Train disruptions in the Netherlands for 2025 (6,676 rows), from the open data of
[Rijden de Treinen](https://www.rijdendetreinen.nl/en/open-data/disruptions), released under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Data source: Rijden de Treinen.

Used only for the year-to-date comparison with our own NS data (roadmap, iteration 5e). It has no
NS ids, so it cannot be joined to our rows; it supports year-over-year comparison only.

Columns (see the source page for the full documentation):

| Column | Meaning |
|---|---|
| `rdt_id` | Rijden de Treinen's id for the disruption |
| `ns_lines` | Lines as written in the NS disruption message; not standardised. Matches our `title` field, so it is the column we compare lines with |
| `rdt_lines`, `rdt_lines_id` | Lines linked by Rijden de Treinen from their own line list, based on the affected stations; standardised |
| `rdt_station_names`, `rdt_station_codes` | Stations affected, derived from those line links |
| `cause_nl`, `cause_en` | Cause (the last one used if NS changed it during the disruption) |
| `statistical_cause_nl`, `statistical_cause_en` | Cause kept for statistics (the more descriptive one when the cause was changed to something generic) |
| `cause_group` | Group the cause belongs to |
| `start_time`, `end_time` | Start and end of the disruption (no timezone in the file; check before comparing hours of the day with our data) |
| `duration_minutes` | Duration in minutes |
