"""Read-only probe of the NS Disruptions API. Prints counts and field names only.

Answers two questions before we redesign the pipeline:
  1. What does the feed return WITHOUT isActive=true (future items? ended items? which fields are filled)?
  2. Does the single-item endpoint (/v3/{type}/{id}) still serve old disruptions?

Usage (from the project root, with NS_API_KEY in your .env):
    python scripts/probe_ns_api.py                      # test 1 only
    python scripts/probe_ns_api.py 7004492 7006819      # test 1 + test 2 (type defaults to maintenance)
    python scripts/probe_ns_api.py disruption:123456    # test 2 for an id of another type

The API key is read from the environment and is never printed.
"""
import os
import sys
from collections import Counter
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

BASE_URL = "https://gateway.apiportal.ns.nl/disruptions/v3"

# Fields the current pipeline does not store but the API spec lists.
NEW_FIELDS = [
    "impact", "phase", "releaseTime", "registrationTime", "period", "topic",
    "summaryAdditionalTravelTime", "alternativeTransportTimespans", "expectedDuration", "end",
]


def parse_time(value):
    """Parse an NS timestamp; returns an aware datetime or None."""
    if not value:
        return None
    for parser in (
        lambda v: datetime.fromisoformat(v),
        lambda v: datetime.strptime(v, "%Y-%m-%dT%H:%M:%S%z"),
    ):
        try:
            parsed = parser(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def is_filled(value):
    """True if a field carries information (not None, empty string, empty list or empty dict)."""
    return value not in (None, "", [], {})


def count_stations(item):
    sections = item.get("publicationSections") or []
    return sum(len(((s.get("section") or {}).get("stations")) or []) for s in sections)


def summarize_feed(items, now):
    """Return printable lines describing a list of disruption items."""
    lines = [f"items returned: {len(items)}"]

    by_type_active = Counter((i.get("type"), bool(i.get("isActive"))) for i in items)
    lines.append("by (type, isActive):")
    for (kind, active), n in sorted(by_type_active.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        lines.append(f"  {kind!s:12} isActive={active!s:5} -> {n}")

    starts = [t for t in (parse_time(i.get("start")) for i in items) if t]
    if starts:
        lines.append(f"start range: {min(starts):%Y-%m-%d} .. {max(starts):%Y-%m-%d}  ({len(items) - len(starts)} items without start)")
    ends = [t for t in (parse_time(i.get("end")) for i in items) if t]
    lines.append(f"ended already (end < now): {sum(1 for t in ends if t < now)}   with end in the future: {sum(1 for t in ends if t >= now)}   without end: {len(items) - len(ends)}")
    future_starts = sum(1 for t in starts if t > now)
    lines.append(f"not started yet (start > now): {future_starts}")

    lines.append("how often each new field is filled (per type):")
    for kind in sorted({str(i.get('type')) for i in items}):
        subset = [i for i in items if str(i.get("type")) == kind]
        filled = {f: sum(1 for i in subset if is_filled(i.get(f))) for f in NEW_FIELDS}
        with_stations = sum(1 for i in subset if count_stations(i) > 0)
        parts = ", ".join(f"{f}={n}" for f, n in filled.items())
        lines.append(f"  {kind} (n={len(subset)}): {parts}, with_stations={with_stations}")
    return lines


def describe_item(item):
    """Printable lines for one disruption fetched by id (no free text, only structure)."""
    lines = [
        f"  type={item.get('type')} isActive={item.get('isActive')} start={item.get('start')} end={item.get('end')}",
        "  fields filled: " + ", ".join(f for f in NEW_FIELDS if is_filled(item.get(f))),
        f"  stations in publicationSections: {count_stations(item)}",
    ]
    return lines


def get(session, url, key, params=None):
    return session.get(
        url,
        headers={"Ocp-Apim-Subscription-Key": key, "Accept-Language": "en"},
        params=params,
        timeout=30,
    )


def main(argv):
    load_dotenv()
    key = os.environ.get("NS_API_KEY")
    if not key:
        sys.exit("NS_API_KEY is missing (put it in .env or the environment)")

    now = datetime.now(timezone.utc)
    session = requests.Session()

    print("=== TEST 1a: feed with isActive=true (what the pipeline fetches today) ===")
    r = get(session, BASE_URL, key, {"isActive": "true"})
    print(f"HTTP {r.status_code}")
    if r.ok:
        print("\n".join(summarize_feed(r.json(), now)))

    print("\n=== TEST 1b: feed WITHOUT isActive ===")
    r = get(session, BASE_URL, key)
    print(f"HTTP {r.status_code}")
    if r.ok:
        print("\n".join(summarize_feed(r.json(), now)))

    ids = argv[1:]
    if ids:
        print("\n=== TEST 2: single-item endpoint for old ids ===")
        for arg in ids:
            kind, _, item_id = arg.rpartition(":")
            kind = (kind or "maintenance").lower()
            r = get(session, f"{BASE_URL}/{kind}/{item_id}", key)
            print(f"{kind}:{item_id} -> HTTP {r.status_code}")
            if r.ok:
                print("\n".join(describe_item(r.json())))


if __name__ == "__main__":
    main(sys.argv)
