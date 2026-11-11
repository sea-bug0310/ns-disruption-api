"""Load NS station names and their countries into ref_station_names (sql/006).

Run it once after sql/006 has been executed, and again whenever you want to refresh the list
(stations rarely change). From the project root, with NS_API_KEY and DATABASE_URL in your .env:

    python scripts/load_stations.py

It replaces the content of ref_station_names in one transaction, so a failure leaves the old
list untouched. The keys are read from the environment and never printed.

The last step prints the stops in line titles that match no station name. Those lines are
counted as domestic (see sql/006), so check the list for foreign places that were missed.
"""
import os
import sys

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import execute_values

URL = "https://gateway.apiportal.ns.nl/nsapp-stations/v2"
COUNTRY_CODES = "nl,d,b,f,gb"

# The API returns about 740 stations for these countries. A much smaller answer is more likely a
# bad response than a real change, and replacing the table with it would break the filter.
MIN_EXPECTED_STATIONS = 300


def fetch_stations(api_key):
    response = requests.get(
        URL,
        headers={"Ocp-Apim-Subscription-Key": api_key, "Accept-Language": "en"},
        params={"includeNonPlannableStations": "true", "countryCodes": COUNTRY_CODES},
        timeout=30,
    )
    response.raise_for_status()

    data = response.json()
    stations = data.get("payload", data) if isinstance(data, dict) else data

    if not isinstance(stations, list):
        raise ValueError("Expected a list of stations from the NS stations API")

    return stations


def name_rows(stations):
    """One (name, country) row per name form of every station: long, medium and short name and
    all synonyms. Titles in the disruption feed do not always use the long name."""
    rows = set()

    for station in stations:
        country = (station.get("land") or "").strip().upper()
        names = station.get("namen") or {}

        forms = [names.get("lang"), names.get("middel"), names.get("kort")]
        forms += station.get("synoniemen") or []

        for name in forms:
            if name and country:
                rows.add((name.strip(), country))

    return sorted(rows)


def replace_names(conn, rows):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM ref_station_names")
        execute_values(cur, "INSERT INTO ref_station_names (name, country) VALUES %s", rows)
    conn.commit()


UNMATCHED_STOPS_SQL = """
    SELECT stop, count(DISTINCT id) AS items
    FROM (
        SELECT d.id,
               unnest(string_to_array(btrim(regexp_replace(btrim(part), '[. ]+$', '')), ' - ')) AS stop
        FROM dashboard_disruptions d,
             unnest(string_to_array(d.title, ';')) AS part
    ) AS stops
    WHERE stop <> '' AND stop_country_class(stop) IS NULL
    GROUP BY stop
    ORDER BY items DESC, stop
"""


def main():
    load_dotenv()
    api_key = os.environ.get("NS_API_KEY")
    database_url = os.environ.get("DATABASE_URL")

    if not api_key or not database_url:
        sys.exit("NS_API_KEY and DATABASE_URL must be set (see .env)")

    stations = fetch_stations(api_key)
    print(f"Fetched {len(stations)} stations")

    if len(stations) < MIN_EXPECTED_STATIONS:
        sys.exit(
            f"Only {len(stations)} stations returned (expected at least "
            f"{MIN_EXPECTED_STATIONS}); leaving ref_station_names unchanged"
        )

    rows = name_rows(stations)
    conn = psycopg2.connect(database_url)

    try:
        replace_names(conn, rows)
        print(f"Loaded {len(rows)} station names into ref_station_names")

        with conn.cursor() as cur:
            cur.execute(UNMATCHED_STOPS_SQL)
            unmatched = cur.fetchall()
    finally:
        conn.close()

    if unmatched:
        print("\nStops in line titles that match no station (counted as domestic):")
        for stop, items in unmatched:
            print(f"  {items:5d}  {stop}")
    else:
        print("\nEvery stop in the line titles matches a station.")


if __name__ == "__main__":
    main()
