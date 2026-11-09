import os
import json
import hashlib
import psycopg2
from psycopg2.extras import Json
from dotenv import load_dotenv
import requests
import logging
import time
from pathlib import Path
from datetime import datetime, timezone
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


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

# If the feed is empty but at least this many disruptions are open in the DB
# (scheduled or active), treat it as a suspicious response and do not mass-close
# them. Tune from real data.
EMPTY_FEED_GUARD_THRESHOLD = 5

# Item types we know how to store. The `disruptions` table has a CHECK on this list, so an
# unknown type is skipped (and logged) instead of making the whole run fail.
KNOWN_TYPES = {"DISRUPTION", "MAINTENANCE", "CALAMITY"}

# --- Hash gate (roadmap decision 10) ---
# The hash decides "did this item change since the last run?". It is computed over the whole
# item in canonical form (sorted keys, no whitespace), minus the fields listed here. The list is
# empty on purpose: the volatility probe found no noisy fields. It is a denylist, not an
# allowlist, so a field NS adds later still counts as a change.
# Arrays are NOT sorted. If you change anything that affects the hash (this list or the
# canonical form), bump HASH_VERSION: every item then hashes differently once.
VOLATILE_FIELDS = ()
HASH_VERSION = 1

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

    # No isActive filter: we also want items that start in the future (planned maintenance).
    # "In the feed" therefore no longer means "active", see compute_status().
    response = session.get(
        URL,
        headers=headers,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list):
        raise ValueError("Expected API response to be a list")

    return data

# ---------------- Parsing helpers (no database, easy to test) ----------------

def join_unique(values):
    #when the item is a list, it can contain multiple events inside it,
    # so we want to join them together in a readable way.
    cleaned = []

    for value in values:
        if value and value not in cleaned:
            cleaned.append(value)

    return " | ".join(cleaned)


def blank_to_none(value):
    """NS sometimes sends empty strings; the database wants NULL for 'no value'."""
    return value if value not in ("", None) else None


def parse_time(value):
    """Parse an NS timestamp (e.g. 2026-11-07T09:15:00+0100) into an aware datetime, or None.

    datetime.fromisoformat only understands '+0100' from Python 3.11 on (the workflow runs 3.11,
    the local conda env 3.12). The strptime fallback only matters on older Pythons.
    """
    if not value:
        return None

    parsed = None
    for parser in (
        datetime.fromisoformat,
        lambda v: datetime.strptime(v, "%Y-%m-%dT%H:%M:%S%z"),
    ):
        try:
            parsed = parser(value)
            break
        except ValueError:
            continue

    if parsed is None:
        return None

    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def compute_status(start_time, now):
    """Lifecycle status of an item that IS in the feed (roadmap, 'Lifecycle status').

    Start in the future -> scheduled. Start passed, or no start at all (CALAMITY) -> active.
    The resolved / cancelled statuses are set in close_vanished_disruptions().
    """
    start = parse_time(start_time)

    if start is not None and start > now:
        return "scheduled"

    return "active"


def compute_payload_hash(item):
    """Hash of the whole item in canonical form, minus VOLATILE_FIELDS. See HASH_VERSION."""
    hashable = {key: value for key, value in item.items() if key not in VOLATILE_FIELDS}
    canonical = json.dumps(
        hashable, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_consequences(publication_sections):
    consequence_descriptions = []
    consequence_levels = []

    # use .get() to avoid KeyError if key is missing
    for section in publication_sections or []:
        consequence = section.get("consequence") or {}

        consequence_descriptions.append(consequence.get("description"))
        consequence_levels.append(consequence.get("level"))

    return {
        "consequence_description": join_unique(consequence_descriptions),
        "consequence_level": join_unique(consequence_levels),
    }


def parse_timespans(timespans):
    situation_labels = []
    cause_labels = []

    for timespan in timespans or []:
        situation = timespan.get("situation") or {}
        cause = timespan.get("cause") or {}

        situation_labels.append(situation.get("label"))
        cause_labels.append(cause.get("label"))

    return {
        "situation_label": join_unique(situation_labels),
        "cause_label": join_unique(cause_labels),
    }


def parse_disruption(disruption, now):
    """Turn one raw NS item into a row for the `disruptions` table."""
    expected_duration = disruption.get("expectedDuration") or {}
    phase = disruption.get("phase") or {}
    impact = disruption.get("impact") or {}
    travel_time = disruption.get("summaryAdditionalTravelTime") or {}

    consequences = parse_consequences(disruption.get("publicationSections"))
    timespan_data = parse_timespans(disruption.get("timespans"))

    return {
        "id": disruption.get("id"),
        "disruption_type": disruption.get("type"),
        "status": compute_status(disruption.get("start"), now),
        "local": bool(disruption.get("local", False)),
        "title": disruption.get("title"),
        "topic": disruption.get("topic"),

        "start_time": blank_to_none(disruption.get("start")),
        "end_time": blank_to_none(disruption.get("end")),
        "registration_time": blank_to_none(disruption.get("registrationTime")),
        "release_time": blank_to_none(disruption.get("releaseTime")),
        "period": disruption.get("period"),

        "phase_id": phase.get("id"),
        "phase_label": phase.get("label"),
        "impact": impact.get("value"),

        "expected_duration_description": expected_duration.get("description"),
        "expected_duration_end_time": blank_to_none(expected_duration.get("endTime")),

        "additional_travel_min_minutes": travel_time.get("minimumDurationInMinutes"),
        "additional_travel_max_minutes": travel_time.get("maximumDurationInMinutes"),

        "consequence_description": consequences["consequence_description"],
        "consequence_level": consequences["consequence_level"],
        "situation_label": timespan_data["situation_label"],
        "cause_label": timespan_data["cause_label"],

        "payload_hash": compute_payload_hash(disruption),
    }


# ---------------- Database writes ----------------

# Columns of `disruptions` that come straight from parse_disruption(). first_seen_at and
# last_seen_at are added in the SQL below. The statement is built from this list so a column
# only has to be named once. The names are fixed strings in this file, never user input.
DISRUPTION_COLUMNS = [
    "id", "disruption_type", "status", "local", "title", "topic",
    "start_time", "end_time", "registration_time", "release_time", "period",
    "phase_id", "phase_label", "impact",
    "expected_duration_description", "expected_duration_end_time",
    "additional_travel_min_minutes", "additional_travel_max_minutes",
    "consequence_description", "consequence_level", "situation_label", "cause_label",
    "payload_hash",
]

UPSERT_DISRUPTION_SQL = """
    INSERT INTO disruptions ({columns}, first_seen_at, last_seen_at)
    VALUES ({placeholders}, %(now)s, %(now)s)
    ON CONFLICT (id) DO UPDATE SET
        {updates},
        last_seen_at = EXCLUDED.last_seen_at
""".format(
    columns=", ".join(DISRUPTION_COLUMNS),
    placeholders=", ".join(f"%({c})s" for c in DISRUPTION_COLUMNS),
    # first_seen_at is deliberately not updated: it keeps the first run that saw the item
    updates=",\n        ".join(f"{c} = EXCLUDED.{c}" for c in DISRUPTION_COLUMNS if c != "id"),
)


def upsert_disruption(cur, disruption, now):
    cur.execute(UPSERT_DISRUPTION_SQL, {**disruption, "now": now})


def insert_payload(cur, disruption, raw, now):
    """Store the raw item as received. One row per item VERSION, not per run."""
    cur.execute("""
        INSERT INTO disruption_payloads (
            disruption_id, payload_hash, hash_version, payload, first_seen_at
        )
        VALUES (%s, %s, %s, %s, %s)
    """, (disruption["id"], disruption["payload_hash"], HASH_VERSION, Json(raw), now))


def load_known_hashes(cur):
    """id -> stored payload hash, for every item we already have."""
    cur.execute("SELECT id, payload_hash FROM disruptions")
    return dict(cur.fetchall())


def touch_unchanged(cur, ids_by_status, now):
    """Unchanged items: only last_seen_at and status move.

    Status has to be refreshed even when NS did not change the item: a scheduled item becomes
    active when its start time passes. This also re-opens an item that was closed by mistake
    (it is back in the feed), see ADR 0001.
    """
    for status, ids in ids_by_status.items():
        if not ids:
            continue
        cur.execute("""
            UPDATE disruptions
            SET status = %s, last_seen_at = %s
            WHERE id = ANY(%s)
        """, (status, now, ids))


def close_vanished_disruptions(cur, seen_ids, now):
    """
    Items that were scheduled/active but are no longer in the feed:
      start had passed        -> resolved
      start still in future   -> cancelled

    last_seen_at is deliberately NOT touched here: it must keep meaning
    "last run in which the disruption was present in the NS feed".
    """
    if not seen_ids:
        # Guard: an empty feed while many items are open is more likely
        # a bad API response than a real "everything resolved at once".
        cur.execute(
            "SELECT count(*) FROM disruptions WHERE status IN ('scheduled', 'active')"
        )
        currently_open = cur.fetchone()[0]

        if currently_open >= EMPTY_FEED_GUARD_THRESHOLD:
            log.warning(
                "Feed is empty but %s disruptions are open - skipping close step",
                currently_open,
            )
            return

    cur.execute("""
        UPDATE disruptions
        SET status = CASE
            WHEN start_time IS NOT NULL AND start_time > %(now)s THEN 'cancelled'
            ELSE 'resolved'
        END
        WHERE status IN ('scheduled', 'active')
          AND NOT (id = ANY(%(seen_ids)s))
    """, {"now": now, "seen_ids": seen_ids})


# ---------------- Run bookkeeping (ingestion_runs) ----------------
# The run row is written in its own commits, so a failed run still leaves a record
# (a rollback of the data must not also roll back the record that it failed).

def start_run(conn, cur, now):
    cur.execute(
        "INSERT INTO ingestion_runs (started_at) VALUES (%s) RETURNING id", (now,)
    )
    run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def finish_run(conn, cur, run_id, status, items_fetched=None, items_changed=None, error=None):
    cur.execute("""
        UPDATE ingestion_runs
        SET finished_at = now(), status = %s, items_fetched = %s, items_changed = %s, error = %s
        WHERE id = %s
    """, (status, items_fetched, items_changed, error, run_id))
    conn.commit()


def abort_run(conn, cur, run_id, error):
    """Undo the data changes of this run and record that it failed."""
    conn.rollback()

    if run_id is None:
        return

    try:
        finish_run(conn, cur, run_id, "failed", error=str(error)[:500])
    except Exception:
        # Do not hide the original error behind a bookkeeping error.
        log.exception("Could not record the failed run in ingestion_runs")


# ---------------- Main job ----------------

def process_feed(cur, raw_items, now):
    """Write one feed into the database. Returns (items_saved, items_changed)."""
    known_hashes = load_known_hashes(cur)

    seen_ids = []
    changed_count = 0
    unchanged_ids_by_status = {"scheduled": [], "active": []}

    for raw in raw_items:
        disruption = parse_disruption(raw, now)

        if not disruption["id"]:
            log.warning("Skipping disruption without id: %s", raw)
            continue

        if disruption["disruption_type"] not in KNOWN_TYPES:
            log.error(
                "Skipping %s: unknown type %r (add it to KNOWN_TYPES and the table CHECK)",
                disruption["id"], disruption["disruption_type"],
            )
            continue

        if disruption["id"] in seen_ids:
            log.warning("Duplicate id in feed, skipping the second copy: %s", disruption["id"])
            continue

        seen_ids.append(disruption["id"])

        if known_hashes.get(disruption["id"]) == disruption["payload_hash"]:
            unchanged_ids_by_status[disruption["status"]].append(disruption["id"])
            continue

        # New item, or NS changed it: save the parsed row and keep this version of the raw JSON.
        # The row goes first because disruption_payloads has a foreign key to it.
        upsert_disruption(cur, disruption, now)
        insert_payload(cur, disruption, raw, now)
        changed_count += 1

    touch_unchanged(cur, unchanged_ids_by_status, now)
    close_vanished_disruptions(cur, seen_ids, now)

    return len(seen_ids), changed_count


def run_ingestion():
    now = datetime.now(timezone.utc)

    conn = get_connection()
    cur = conn.cursor()
    run_id = None

    try:
        run_id = start_run(conn, cur, now)

        raw_items = fetch_disruptions()
        log.info("Fetched %s items", len(raw_items))

        saved_count, changed_count = process_feed(cur, raw_items, now)

        conn.commit()
        finish_run(
            conn, cur, run_id, "success",
            items_fetched=len(raw_items), items_changed=changed_count,
        )
        log.info(
            "Saved %s items to database (%s new or changed)", saved_count, changed_count
        )

    except requests.exceptions.Timeout as e:
        # undo a group of database changes if the request fails,
        # to avoid saving incomplete or inconsistent data
        abort_run(conn, cur, run_id, e)
        log.error("Request timed out")
        raise

    except requests.exceptions.HTTPError as e:
        abort_run(conn, cur, run_id, e)
        status = e.response.status_code if e.response else "unknown"
        log.error("HTTP error %s: %s", status, e)
        raise

    except requests.exceptions.RequestException as e:
        abort_run(conn, cur, run_id, e)
        log.error("Network error: %s", e)
        raise

    except Exception as e:
        abort_run(conn, cur, run_id, e)
        log.exception("Unexpected error: %s", e)
        raise

    finally:
        conn.close()


# ---------------- Scheduler ----------------

if __name__ == "__main__":
    log.info("Starting NS disruption ingestion")
    run_ingestion()
    log.info("Ingestion script finished")
