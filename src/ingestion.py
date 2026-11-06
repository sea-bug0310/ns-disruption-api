import os
import psycopg2
from dotenv import load_dotenv
import requests
import logging
import time
from pathlib import Path
from datetime import datetime, timezone
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from math import radians, sin, cos, sqrt, atan2


# --- Logging setup ---
LOG_PATH = Path(__file__).parent / "ingestion.log"

# log level in python: debug < info < warning < error < critical
# log everything above info to file
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH), # write log to ingestion.log file
        logging.StreamHandler() # print to terminal too
    ]
)

""" If your project grows and you import this script into another one, your log entries will 
explicitly state exactly which file/module generated the log
"""

log = logging.getLogger(__name__)

load_dotenv()
API_KEY = os.environ.get("NS_API_KEY")
URL = "https://gateway.apiportal.ns.nl/disruptions/v3"
DATABASE_URL = os.environ.get("DATABASE_URL")

# If the feed is empty but at least this many disruptions are active in the DB,
# treat it as a suspicious response and do not mass-close them. Tune from real data.
EMPTY_FEED_GUARD_THRESHOLD = 5

# ------------------DB CONNECTION --------------
def get_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is missing")
    return psycopg2.connect(DATABASE_URL)

# ---------------- HTTP ----------------
def get_session():
    # no need to write complex retry logic & while loop
    # previously we use requests.get(), if a seve blinks for a milisec, the request fails immediately
    session = requests.Session()

    """Python will return the final error response object cleanly, 
    rather than throwing a hard code exception that crashes your entire script"""
    
    retry = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False
    )

    #glues your retry rules to the session
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)

    return session

def fetch_disruptions():
    if not API_KEY:
        raise RuntimeError("NS_API_KEY environment variable is missing")

    # get your resilient session with retry logic
    session = get_session()

    headers = {
        "Ocp-Apim-Subscription-Key": API_KEY,
        "Accept-Language": "en"
    }

    response = session.get(
        URL,
        headers=headers,
        params={"isActive": "true"},
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list):
        raise ValueError("Expected API response to be a list")

    return data

# ---------------- Parsing helpers ----------------

def join_unique(values):
    #when the item is a list, it can contain multiple events inside it, 
    # so we want to join them together in a readable way.
    cleaned = []

    for value in values:
        if value and value not in cleaned:
            cleaned.append(value)

    return " | ".join(cleaned)


def haversine_km(lat1, lon1, lat2, lon2):
    """Calculate straight-line distance between two coordinates in km."""
    earth_radius_km = 6371

    lat1, lon1, lat2, lon2 = map(
        radians, [lat1, lon1, lat2, lon2]
    )

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        sin(dlat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    )

    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return earth_radius_km * c


def calculate_section_km(stations):
    """
    Calculate affected distance for one publication section.
    Sums the distance between consecutive affected stations.
    """
    if not stations or len(stations) < 2:
        return 0

    total_km = 0

    for i in range(len(stations) - 1):
        coord_1 = stations[i].get("coordinate", {})
        coord_2 = stations[i + 1].get("coordinate", {})

        lat1 = coord_1.get("lat")
        lon1 = coord_1.get("lng")
        lat2 = coord_2.get("lat")
        lon2 = coord_2.get("lng")

        if None in [lat1, lon1, lat2, lon2]:
            continue

        total_km += haversine_km(lat1, lon1, lat2, lon2)

    return total_km


def parse_publication_sections(publication_sections):
    from_stations = []
    to_stations = []

    from_station_lats = []
    from_station_lngs = []
    to_station_lats = []
    to_station_lngs = []

    consequence_descriptions = []
    consequence_levels = []

    total_km = 0 

    # use .get() to avoid KeyError if key is missing
    for section in publication_sections or []:
        consequence = section.get("consequence", {})

        consequence_section = consequence.get("section", {})
        stations = consequence_section.get("stations", [])

        if stations:
            first_station = stations[0]
            last_station = stations[-1]

            # ---- names ----
            from_stations.append(first_station.get("name"))
            to_stations.append(last_station.get("name"))

            # ---- coordinates ----
            #an't find 'coordinate', don't give me None, give me an empty dictionary instead.
            first_coord = first_station.get("coordinate", {})
            last_coord = last_station.get("coordinate", {})

            from_station_lats.append(first_coord.get("lat"))
            from_station_lngs.append(first_coord.get("lng"))

            to_station_lats.append(last_coord.get("lat"))
            to_station_lngs.append(last_coord.get("lng"))

        consequence_descriptions.append(consequence.get("description"))
        consequence_levels.append(consequence.get("level"))

        #calculate affected km 
        total_km += calculate_section_km(stations)

    return {
        "from_station": join_unique(from_stations),
        "to_station": join_unique(to_stations),

        "from_station_lat": from_station_lats[0] if from_station_lats else None,
        "from_station_lng": from_station_lngs[0] if from_station_lngs else None,

        "to_station_lat": to_station_lats[0] if to_station_lats else None,
        "to_station_lng": to_station_lngs[0] if to_station_lngs else None,

        "consequence_description": join_unique(consequence_descriptions),
        "consequence_level": join_unique(consequence_levels),

        "affected_km": round(total_km, 0),
    }


def parse_timespans(timespans):
    situation_labels = []
    cause_labels = []

    for timespan in timespans or []:
        situation = timespan.get("situation", {})
        cause = timespan.get("cause", {})

        situation_labels.append(situation.get("label"))
        cause_labels.append(cause.get("label"))

    return {
        "situation_label": join_unique(situation_labels),
        "cause_label": join_unique(cause_labels),
    }


def parse_disruption(disruption):
    expected_duration = disruption.get("expectedDuration", {})

    publication_data = parse_publication_sections(
        disruption.get("publicationSections", [])
    )

    timespan_data = parse_timespans(
        disruption.get("timespans", [])
    )

    return {
        "id": disruption.get("id"),
        "title": disruption.get("title"),
        "disruption_type": disruption.get("type"),
        "local": bool(disruption.get("local", False)),
        "is_active": bool(disruption.get("isActive", False)),
        "start_time": disruption.get("start"),
        "end_time": disruption.get("end"),

        "expected_duration_description": expected_duration.get("description"),
        "expected_duration_end_time": expected_duration.get("endTime"),

        "from_station": publication_data["from_station"],
        "from_station_lat": publication_data["from_station_lat"],
        "from_station_lng": publication_data["from_station_lng"],

        "to_station": publication_data["to_station"],
        "to_station_lat": publication_data["to_station_lat"],
        "to_station_lng": publication_data["to_station_lng"],

        "affected_km": publication_data["affected_km"],

        "consequence_description": publication_data["consequence_description"],
        "consequence_level": publication_data["consequence_level"],

        "situation_label": timespan_data["situation_label"],
        "cause_label": timespan_data["cause_label"],
    }


# ---------------- Database writes ----------------

def upsert_disruption(cur, disruption, now):
    cur.execute("""
        INSERT INTO disruptions (
            id,
            disruption_type,
            local,
            title,
            is_active,
            start_time,
            end_time,
            expected_duration_description,
            expected_duration_end_time,
            from_station,
            from_station_lat,
            from_station_lng,
            to_station,
            to_station_lat,
            to_station_lng,
            affected_km,
            consequence_description,
            consequence_level,
            situation_label,
            cause_label,
            first_seen_at,
            last_seen_at
        )
        VALUES (
            %(id)s,
            %(disruption_type)s,
            %(local)s,
            %(title)s,
            %(is_active)s,
            %(start_time)s,
            %(end_time)s,
            %(expected_duration_description)s,
            %(expected_duration_end_time)s,
            %(from_station)s,
            %(from_station_lat)s,
            %(from_station_lng)s,
            %(to_station)s,
            %(to_station_lat)s,
            %(to_station_lng)s,
            %(affected_km)s,
            %(consequence_description)s,
            %(consequence_level)s,
            %(situation_label)s,
            %(cause_label)s,
            %(now)s,
            %(now)s
        )
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            disruption_type = excluded.disruption_type,
            local = excluded.local,
            is_active = excluded.is_active,
            start_time = excluded.start_time,
            end_time = excluded.end_time,
            expected_duration_description = excluded.expected_duration_description,
            expected_duration_end_time = excluded.expected_duration_end_time,
            from_station = excluded.from_station,
            from_station_lat = excluded.from_station_lat,
            from_station_lng = excluded.from_station_lng,
            to_station = excluded.to_station,
            to_station_lat = excluded.to_station_lat,
            to_station_lng = excluded.to_station_lng,
            affected_km = excluded.affected_km,
            consequence_description = excluded.consequence_description,
            consequence_level = excluded.consequence_level,
            situation_label = excluded.situation_label,
            cause_label = excluded.cause_label,
            last_seen_at = excluded.last_seen_at
    """, {**disruption, "now": now})


def insert_snapshot(cur, disruption, now):
    cur.execute("""
        INSERT INTO disruption_snapshots (
            disruption_id,
            disruption_type,
            local,
            fetched_at,
            is_active,
            start_time,
            end_time,
            title,
            expected_duration_description,
            expected_duration_end_time,
            from_station,
            from_station_lat,
            from_station_lng,
            to_station,
            to_station_lat,
            to_station_lng,
            affected_km,
            consequence_description,
            consequence_level,
            situation_label,
            cause_label
        )
        VALUES (
            %(id)s,
            %(disruption_type)s,
            %(local)s,
            %(now)s,
            %(is_active)s,
            %(start_time)s,
            %(end_time)s,
            %(title)s,
            %(expected_duration_description)s,
            %(expected_duration_end_time)s,
            %(from_station)s,
            %(from_station_lat)s,
            %(from_station_lng)s,
            %(to_station)s,
            %(to_station_lat)s,
            %(to_station_lng)s,
            %(affected_km)s,
            %(consequence_description)s,
            %(consequence_level)s,
            %(situation_label)s,
            %(cause_label)s
        )
    """, {**disruption, "now": now})


def mark_resolved_disruptions(cur, active_ids):
    """
    Mark disruptions that vanished from the feed as resolved.

    last_seen_at is deliberately NOT touched here: it must keep meaning
    "last run in which the disruption was present in the NS feed".
    """
    if not active_ids:
        # Guard: an empty feed while many disruptions are active is more likely
        # a bad API response than a real "everything resolved at once".
        cur.execute("SELECT count(*) FROM disruptions WHERE is_active = TRUE")
        currently_active = cur.fetchone()[0]

        if currently_active >= EMPTY_FEED_GUARD_THRESHOLD:
            log.warning(
                "Feed is empty but %s disruptions are active - skipping close step",
                currently_active,
            )
            return

        cur.execute("UPDATE disruptions SET is_active = FALSE WHERE is_active = TRUE")
        return

    cur.execute("""
        UPDATE disruptions
        SET is_active = FALSE
        WHERE is_active = TRUE
          AND NOT (id = ANY(%s))
    """, (active_ids,))

# ---------------- Main job ----------------

def run_ingestion():
    now = datetime.now(timezone.utc).isoformat()

    conn = get_connection()
    cur = conn.cursor()

    try:
        raw_disruptions = fetch_disruptions()
        log.info("Fetched %s active disruptions", len(raw_disruptions))

        saved_count = 0
        active_ids = []

        for raw in raw_disruptions:
            disruption = parse_disruption(raw)

            if not disruption["id"]:
                log.warning("Skipping disruption without id: %s", raw)
                continue
            
            active_ids.append(disruption["id"])
            disruption["is_active"] = True

            upsert_disruption(cur, disruption, now)
            insert_snapshot(cur, disruption, now)

            saved_count += 1

        mark_resolved_disruptions(cur, active_ids)

        conn.commit()
        log.info("Saved %s disruptions to database", saved_count)

    except requests.exceptions.Timeout:
        # undo a group of database changes if the request fails, 
        # to avoid saving incomplete or inconsistent data
        conn.rollback()
        log.error("Request timed out")
        raise

    except requests.exceptions.HTTPError as e:
        conn.rollback()
        status = e.response.status_code if e.response else "unknown"
        log.error("HTTP error %s: %s", status, e)
        raise

    except requests.exceptions.RequestException as e:
        conn.rollback()
        log.error("Network error: %s", e)
        raise

    except Exception as e:
        conn.rollback()
        log.exception("Unexpected error: %s", e)
        raise

    finally:
        conn.close()


# ---------------- Scheduler ----------------

if __name__ == "__main__":
    log.info("Starting NS disruption ingestion")
    run_ingestion()
    log.info("Ingestion script finished")
