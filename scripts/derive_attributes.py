#!/usr/bin/env python3
"""Derive structured amenities.activities / water_feature / toilet_type from the
free text we already have (amenities.amenities_raw, campsites.overview, name).

This is a stopgap until the scrapers pull structured fields directly: it only
lights up sites that have descriptive text (fs.usda + RIDB, ~1370 of 2440).
Dispersed / ReserveCalifornia rows mostly have no prose and stay sparse.

    python scripts/derive_attributes.py
    python scripts/derive_attributes.py --dry-run --limit 40

Idempotent: every run recomputes from source text and overwrites.
"""

import re
import argparse

from _pipeline import get_conn, scrape_run

# tag -> regex of trigger phrases (searched against name + amenities_raw + overview)
ACTIVITY_RULES = {
    "hiking": r"\bhik(?:e|ing)|\btrail(?:head|s)?\b|day hike|nature trail",
    "backpacking": r"backpack|wilderness permit|pct\b|pacific crest",
    "fishing": r"\bfish(?:ing)?\b|trout|angler",
    "swimming": r"\bswim(?:ming)?\b|swimming hole",
    "boating": r"\bboat(?:ing|ramp| launch|s)?\b|marina|boat-in",
    "paddling": r"kayak|canoe|paddle|paddling|stand-?up paddle|\bsup\b|rafting",
    "whitewater": r"whitewater|white water|class (?:ii|iii|iv|v)\b",
    "mountain biking": r"mountain bik|mtb\b|single ?track",
    "biking": r"\bbicycl|\bbik(?:e|ing)\b|bike path|cycling",
    "horseback riding": r"horse(?:back)?|equestrian|pack station|stock use|corral",
    "rock climbing": r"rock climb|bouldering|climbing route|crag\b",
    "off-roading": r"\bohv\b|\batv\b|off-?road|4x4|four-wheel|jeep trail|staging area",
    "wildlife viewing": r"wildlife (?:viewing|watching)|wildlife photography|tortoise|elk|deer viewing",
    "birding": r"bird(?:ing|watching| watching)|birdwatch",
    "hunting": r"\bhunt(?:ing)?\b",
    "photography": r"photograph|scenic view|sightsee|vista point",
    "picnicking": r"picnic",
    "winter sports": r"\bski(?:ing)?\b|snowshoe|snowmobile|cross-country ski|sledding|winter recreation",
    "stargazing": r"stargaz|dark sky|astronom",
    "swimming beach": r"\bbeach\b|sandy shore",
    "OHV staging": r"ohv staging|off-highway vehicle staging",
}

# water_feature: first match wins, most specific first
WATER_RULES = [
    ("hot spring", r"hot spring"),
    ("waterfall", r"waterfall|\bfalls\b"),
    ("ocean", r"\bocean|pacific coast|sea cliff|tidepool|\bsurf\b"),
    ("lake", r"\blake\b"),
    ("reservoir", r"reservoir"),
    ("river", r"\briver\b"),
    ("creek", r"\bcreek\b|\bstream\b|\bbrook\b"),
    ("pond", r"\bpond\b|\blagoon\b"),
]

TOILET_RULES = [
    ("flush", r"flush (?:toilet|restroom|vault)|flushing toilet"),
    ("vault", r"vault toilet|pit toilet|vault restroom|vault privy|pit privy"),
    ("none", r"no (?:toilet|restroom)s?\b|toilets?: none"),
]


def _scan(text, rules):
    hits = []
    for tag, pat in rules:
        if re.search(pat, text, re.I):
            hits.append(tag)
    return hits


def derive(name, amenities_raw, overview, body_of_water, restrooms):
    blob = " ".join(x for x in (name, amenities_raw, overview) if x)

    activities = sorted({
        tag for tag, pat in ACTIVITY_RULES.items() if re.search(pat, blob, re.I)
    })
    # collapse a couple of near-duplicates
    if "mountain biking" in activities and "biking" in activities:
        activities.remove("biking")
    if "swimming beach" in activities:
        activities.remove("swimming beach")
        for t in ("swimming", "beach access"):
            if t not in activities:
                activities.append(t)
    if "OHV staging" in activities:
        activities.remove("OHV staging")
        if "off-roading" not in activities:
            activities.append("off-roading")
    activities = sorted(set(activities))

    water_feature = None
    for tag, pat in WATER_RULES:
        if re.search(pat, blob, re.I):
            water_feature = tag
            break
    if water_feature is None and body_of_water:
        water_feature = "water nearby"

    toilet_type = None
    for tag, pat in TOILET_RULES:
        if re.search(pat, blob, re.I):
            toilet_type = tag
            break
    if toilet_type is None and restrooms:
        toilet_type = "restrooms"

    return activities, water_feature, toilet_type


SELECT_SQL = """
    SELECT c.id, c.name, a.amenities_raw, c.overview, a.body_of_water, a.restrooms
    FROM campsites c
    JOIN amenities a ON a.campsite_id = c.id
    ORDER BY c.id
"""

UPDATE_SQL = """
    UPDATE amenities
    SET activities = %(acts)s, water_feature = %(water)s, toilet_type = %(toilet)s
    WHERE campsite_id = %(id)s
"""


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    with get_conn() as conn, scrape_run("derive_attributes") as run:
        sel = conn.cursor()
        q = SELECT_SQL + (f" LIMIT {int(args.limit)}" if args.limit else "")
        sel.execute(q)
        rows = sel.fetchall()
        sel.close()

        upd = conn.cursor()
        shown = 0
        for cid, name, araw, overview, bow, restrooms in rows:
            run.seen += 1
            acts, water, toilet = derive(name, araw, overview, bow, restrooms)

            if args.dry_run:
                if shown < 40 and (acts or water or toilet):
                    print(f"[{cid}] {name}")
                    print(f"   activities={acts}")
                    print(f"   water={water!r} toilet={toilet!r}")
                    shown += 1
                continue

            upd.execute(UPDATE_SQL, {"id": cid, "acts": acts, "water": water, "toilet": toilet})
            run.upserted += 1

        if not args.dry_run:
            conn.commit()
        upd.close()


if __name__ == "__main__":
    main()
