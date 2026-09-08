#!/usr/bin/env python3
"""Backfill amenities for ReserveCalifornia campgrounds from parks.ca.gov.

ReserveCalifornia's booking API (UseDirect / Tyler) carries no amenity or
activity data at all, so the ~510 `source = 'reserve_california'` campsites have
almost empty `amenities` rows. The California State Parks website *does* publish,
per park, an "Activities and Facilities" list. This job:

  1. reads the park index (`?page_id=21805`) -> {park name: page_id}
  2. fuzzy-matches each distinct `campsites.managing_unit` (the park name we
     stored at scrape time, e.g. "Anza-Borrego Desert SP") to an index park
  3. fetches that park page once, parses the Activities/Facilities <li> list
  4. maps the known phrases to our vocab and writes them to every RC campsite
     in that park

The data is **park-level**, not campground-level: every campground in a park
gets the same set. It is applied accumulatively (activities UNIONed, toilet /
water only fill a NULL) exactly like `derive_attributes`, so running it before
or after the other attribute jobs is safe.

    python scripts/scrape_parks_ca.py
    python scripts/scrape_parks_ca.py --dry-run
    python scripts/scrape_parks_ca.py --limit 20 --sleep 0.2
"""

import re
import time
import html
import argparse

import requests
from rapidfuzz import fuzz

from _pipeline import get_conn, scrape_run

INDEX_URL = "https://www.parks.ca.gov/?page_id=21805"
PARK_URL = "https://www.parks.ca.gov/?page_id={pid}"

# managing_unit abbreviations -> the words parks.ca.gov spells out
_ABBR = [
    (r"\bSHP\b", "State Historic Park"),
    (r"\bSRA\b", "State Recreation Area"),
    (r"\bSVRA\b", "State Vehicular Recreation Area"),
    (r"\bSNR\b", "State Natural Reserve"),
    (r"\bSMR\b", "State Marine Reserve"),
    (r"\bSP\b", "State Park"),
    (r"\bSB\b", "State Beach"),
    (r"\bSM\b", "State Monument"),
    (r"\bMem\b", "Memorial"),
]

# lodging-type suffixes RC appends to a park name for its cabin / glamp
# inventory ("Samuel P. Taylor SP Cabins") — the amenities are the park's.
_SUFFIX_RE = re.compile(
    r"\b(cabins?|tent cabins?|glamping|glamp(?:ing)? sites?|hotels? and cottages?"
    r"|beach cottages?|vintage trailers?|the holidays.*)$", re.I
)


def normalize_park(name):
    if not name:
        return ""
    s = html.unescape(name).replace("®", "")
    s = s.replace("&", "and")
    for pat, full in _ABBR:
        s = re.sub(pat, full, s)
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    s = re.sub(r"\s+", " ", s).strip()
    s = _SUFFIX_RE.sub("", s).strip()
    return s


# parks.ca.gov "Activities and Facilities" phrase -> our activity tag
_ACTIVITY_MAP = {
    "hiking trails": "hiking",
    "bike trails": "biking",
    "mountain biking": "mountain biking",
    "horseback riding": "horseback riding",
    "equestrian campsites": "horseback riding",
    "fishing": "fishing",
    "swimming": "swimming",
    "boating": "boating",
    "boat ramps": "boating",
    "windsurfing/surfing": "beach access",
    "beach area": "beach access",
    "scuba diving/snorkeling": "swimming",
    "nature & wildlife viewing": "wildlife viewing",
    "picnic areas": "picnicking",
    "off-highway motorcycling": "off-roading",
    "vista point": "photography",
    "environmental camping": "backpacking",
    "hike-and-bike campsites": "biking",
}

# footer / sidebar boilerplate that shares the accordion's <li> markup
_FOOTER_MARKERS = ("Safety Tips", "Dogs in Parks", "Buy It Where", "Free Passes",
                   "Parks Mobile App", "Conditions of Use", "Privacy Policy")


def park_index(session):
    page = session.get(INDEX_URL, timeout=60).text
    # link text can carry a trailing <span>&reg;</span>, so grab everything up to
    # </a> and strip tags / entities before normalising.
    pairs = re.findall(r'page_id=(\d+)"[^>]*>(.*?)</a>', page, re.S)
    out = {}
    for pid, raw in pairs:
        name = html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()
        if len(name) < 5:
            continue
        if not re.search(r"State|Reserve|Redwoods|Wilderness|Park", name):
            continue
        out.setdefault(normalize_park(name), int(pid))
    return out


def parse_park_page(html):
    """(activities set, water bool|None, toilet str|None, restrooms bool|None)."""
    i = html.find('id="collapse200Five"')
    if i < 0:
        return set(), None, None, None
    block = html[i:i + 9000]
    for mark in _FOOTER_MARKERS:
        k = block.find(mark)
        if k > 0:
            block = block[:k]

    labels = []
    for li in re.findall(r"<li>(.*?)</li>", block, re.S):
        txt = re.sub(r"<[^>]+>", "", li)
        txt = txt.replace("&amp;", "&").replace("&nbsp;", " ")
        txt = re.sub(r"&#x[0-9A-Fa-f]+;", "", txt)
        txt = re.sub(r"\s+", " ", txt).strip().lower()
        if txt and len(txt) < 46:
            labels.append(txt)

    acts = {tag for phrase, tag in _ACTIVITY_MAP.items() if phrase in labels}

    water = True if "drinking water available" in labels else None
    restrooms = True if any("restroom" in l for l in labels) else None
    if "restrooms / showers" in labels:
        toilet = "flush"
    elif restrooms:
        toilet = "restrooms"
    else:
        toilet = None
    return acts, water, toilet, restrooms


UPDATE_SQL = """
    UPDATE amenities
    SET activities = ARRAY(
            SELECT DISTINCT e
            FROM unnest(activities || %(acts)s::text[]) AS e
            WHERE e <> ''
        ),
        water        = COALESCE(water, %(water)s),
        restrooms    = COALESCE(restrooms, %(restrooms)s),
        toilet_type  = COALESCE(toilet_type, %(toilet)s)
    WHERE campsite_id = %(id)s
"""


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=None, help="max parks to process")
    p.add_argument("--sleep", type=float, default=0.4)
    p.add_argument("--min-score", type=float, default=88.0,
                   help="rapidfuzz cutoff for park-name matching")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    session = requests.Session()
    session.headers.update({"User-Agent": "campsearch data refresh (parks.ca.gov)"})

    index = park_index(session)
    print(f"{len(index)} parks in the parks.ca.gov index")

    with get_conn() as conn:
        sel = conn.cursor()
        sel.execute(
            "SELECT id, managing_unit FROM campsites "
            "WHERE source = 'reserve_california' AND managing_unit IS NOT NULL"
        )
        rows = sel.fetchall()
        sel.close()

    # group campsite ids by park name
    by_park = {}
    for cid, unit in rows:
        by_park.setdefault(unit, []).append(cid)
    print(f"{len(by_park)} distinct parks across {len(rows)} RC campsites")

    index_items = list(index.items())
    unmatched = []
    processed = 0

    with get_conn() as conn, scrape_run("parks_ca") as run:
        upd = conn.cursor()
        for unit, cids in sorted(by_park.items()):
            norm = normalize_park(unit)
            best_pid, best_score = None, 0.0
            for iname, pid in index_items:
                sc = fuzz.WRatio(norm, iname)
                if sc > best_score:
                    best_pid, best_score = pid, sc
            if best_score < args.min_score:
                unmatched.append((unit, round(best_score, 1)))
                continue

            try:
                html = session.get(PARK_URL.format(pid=best_pid), timeout=60).text
            except Exception as exc:  # noqa: BLE001
                run.errors += 1
                print(f"  ! {unit}: fetch failed: {exc}")
                continue
            acts, water, toilet, restrooms = parse_park_page(html)
            time.sleep(args.sleep)

            if not (acts or water or toilet):
                continue

            processed += 1
            run.seen += len(cids)
            if args.dry_run:
                print(f"  {unit} -> pid {best_pid} ({best_score:.0f})  "
                      f"acts={sorted(acts)} water={water} toilet={toilet}  x{len(cids)} sites")
            else:
                for cid in cids:
                    upd.execute(UPDATE_SQL, {
                        "id": cid, "acts": sorted(acts),
                        "water": water, "restrooms": restrooms, "toilet": toilet,
                    })
                    run.upserted += 1
                conn.commit()

            if args.limit and processed >= args.limit:
                break
        upd.close()

    if unmatched:
        print(f"\n{len(unmatched)} parks had no confident index match:")
        for unit, sc in unmatched[:40]:
            print(f"  {unit}  (best {sc})")


if __name__ == "__main__":
    main()
