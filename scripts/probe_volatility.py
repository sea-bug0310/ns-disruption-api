"""Read-only probe: which fields of a disruption item change between two fetches?

Fetches the feed twice, WAIT minutes apart, then diffs the items that appear in both runs
(matched by id). Fields that differ are either real changes or "volatile" fields that would make
a payload hash change on every run. Arrays whose elements are the same but in a different order
are reported as REORDERED.

Usage (from the project root, with NS_API_KEY in your .env):
    python scripts/probe_volatility.py                       # 5 minutes apart, all items
    python scripts/probe_volatility.py --wait 2              # 2 minutes apart
    python scripts/probe_volatility.py --active-only         # same filter as today's pipeline
    python scripts/probe_volatility.py --save-dir <folder>   # also keep both raw fetches as JSON

The API key is read from the environment and is never printed.
"""
import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

BASE_URL = "https://gateway.apiportal.ns.nl/disruptions/v3"


def fetch(session, key, active_only):
    r = session.get(
        BASE_URL,
        headers={"Ocp-Apim-Subscription-Key": key, "Accept-Language": "en"},
        params={"isActive": "true"} if active_only else None,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        sys.exit("Expected the API to return a list")
    return data


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def short(value, n=70):
    text = canon(value)
    return text if len(text) <= n else text[: n - 3] + "..."


def generalize(path):
    """Replace array indexes with [] so 'timespans[0].start' and 'timespans[1].start' group together."""
    out, i = [], 0
    while i < len(path):
        if path[i] == "[":
            j = path.index("]", i)
            out.append("[]")
            i = j + 1
        else:
            out.append(path[i])
            i += 1
    return "".join(out)


def diff(a, b, path, out):
    """Append (path, kind, a_value, b_value) tuples to out."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            sub = f"{path}.{k}" if path else k
            if k not in a:
                out.append((sub, "ADDED", None, b[k]))
            elif k not in b:
                out.append((sub, "REMOVED", a[k], None))
            else:
                diff(a[k], b[k], sub, out)
    elif isinstance(a, list) and isinstance(b, list):
        if canon(a) == canon(b):
            return
        if sorted(canon(x) for x in a) == sorted(canon(x) for x in b):
            out.append((path, "REORDERED", None, None))
            return
        if len(a) != len(b):
            out.append((path, "LENGTH", len(a), len(b)))
        for i in range(min(len(a), len(b))):
            diff(a[i], b[i], f"{path}[{i}]", out)
    elif a != b:
        out.append((path, "CHANGED", a, b))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=float, default=5.0, help="minutes between the two fetches")
    ap.add_argument("--active-only", action="store_true", help="use isActive=true like the current pipeline")
    ap.add_argument("--save-dir", help="folder to save both raw fetches into")
    args = ap.parse_args()

    load_dotenv()
    key = os.environ.get("NS_API_KEY")
    if not key:
        sys.exit("NS_API_KEY is missing (put it in .env or the environment)")

    session = requests.Session()
    t1 = datetime.now(timezone.utc)
    first = fetch(session, key, args.active_only)
    print(f"fetch 1 at {t1:%H:%M:%S}Z: {len(first)} items", flush=True)

    time.sleep(args.wait * 60)

    t2 = datetime.now(timezone.utc)
    second = fetch(session, key, args.active_only)
    print(f"fetch 2 at {t2:%H:%M:%S}Z: {len(second)} items", flush=True)

    if args.save_dir:
        os.makedirs(args.save_dir, exist_ok=True)
        for name, data in (("fetch1.json", first), ("fetch2.json", second)):
            with open(os.path.join(args.save_dir, name), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        print(f"raw fetches saved in {args.save_dir}")

    by_id_1 = {i.get("id"): i for i in first}
    by_id_2 = {i.get("id"): i for i in second}
    both = [i for i in by_id_1 if i in by_id_2]
    print(f"\nin both fetches: {len(both)}   only in fetch 1: {len(by_id_1) - len(both)}   only in fetch 2: {len(by_id_2) - len(both)}")

    # Order of the items in the feed itself (does not matter for hashing per item, but good to know).
    same_feed_order = [i for i in by_id_1 if i in by_id_2] == [i for i in by_id_2 if i in by_id_1]
    print(f"feed order of shared items identical: {same_feed_order}")

    changed_items = 0
    per_path = defaultdict(Counter)   # generalized path -> Counter of kinds
    examples = {}                     # (generalized path, kind) -> (a, b)
    for item_id in both:
        out = []
        diff(by_id_1[item_id], by_id_2[item_id], "", out)
        if out:
            changed_items += 1
        for path, kind, a, b in out:
            g = generalize(path)
            per_path[g][kind] += 1
            examples.setdefault((g, kind), (a, b))

    print(f"\nitems with at least one difference: {changed_items} of {len(both)}"
          f"  -> a naive full-payload hash would change for {changed_items / len(both):.0%}" if both else "\nno shared items")
    if not per_path:
        print("No differences. Try a longer --wait, or run at a busier time.")
        return

    print("\nfields that differ (generalized path, kind, how many items/places, example):")
    for g in sorted(per_path):
        for kind, n in per_path[g].items():
            a, b = examples[(g, kind)]
            ex = "" if kind == "REORDERED" else f"   e.g. {short(a)} -> {short(b)}"
            print(f"  {g:55} {kind:9} x{n}{ex}")


if __name__ == "__main__":
    main()
