#!/usr/bin/env python3
"""Match campsites to recreation.gov (RIDB) facilities and pull reservation data.

For each campsite (by default only those missing a recreation_facility_id), search
RIDB Facilities by name, pick the best reservable Campground match in the right
RecArea, then upsert:
  - campsites.recreation_facility_id, reservation_url, num_sites, contact_phone,
    overview, managing_unit, last_scraped
  - images (campsite_id, image_url) — has a UNIQUE constraint now
  - reservations (campsite_id) — one row per site

    python scripts/sync_ridb.py                     # sites missing a facility id
    python scripts/sync_ridb.py --all --limit 20    # any site, capped
    python scripts/sync_ridb.py --site-id 759
    python scripts/sync_ridb.py --seed-agencies     # fill agencies.ridb_org_id, then exit

Needs RIDB_API_KEY in the environment / .env. Replaces scripts/recreation_sync.py.
"""

import re
import time
import argparse

import requests

# _pipeline puts the repo root on sys.path, so `config` imports after it.
from _pipeline import get_conn, scrape_run  # noqa: E402
from config import ridb_api_key  # noqa: E402

BASE_URL = "https://ridb.recreation.gov/api/v1"
RATE_LIMIT_DELAY = 0.4
FUZZ_THRESHOLD = 70

# Our agencies.name -> substrings that identify the org in RIDB /organizations.
AGENCY_ORG_HINTS = {
    "US Forest Service": ["forest service"],
    "National Park Service": ["national park service"],
    "Bureau of Land Management": ["bureau of land management"],
}


def headers():
    return {"accept": "application/json", "apikey": ridb_api_key()}


def normalize_name(name):
    if not name:
        return ""
    name = name.lower().strip()
    if name.endswith(" campground"):
        name = name[: -len(" campground")]
    return name


def clean_html(text):
    if not text:
        return None
    return re.sub(r"<[^>]+>", "", text).strip() or None


def _ratio(a, b):
    from rapidfuzz import fuzz

    return fuzz.token_sort_ratio(normalize_name(a), normalize_name(b))


def search_facility(session, name, forest_name=None):
    r = session.get(
        f"{BASE_URL}/facilities",
        headers=headers(),
        params={
            "query": normalize_name(name),
            "limit": 5,
            "offset": 0,
            "full": "true",
            "state": "CA",
            "activity": 9,  # CAMPING
        },
        timeout=30,
    )
    time.sleep(RATE_LIMIT_DELAY)
    if r.status_code != 200:
        return None

    best, best_score = None, 0
    for fac in r.json().get("RECDATA", []):
        if not fac.get("Reservable"):
            continue
        if fac.get("FacilityTypeDescription") != "Campground":
            continue
        if forest_name:
            names = [rc.get("RecAreaName", "").lower() for rc in fac.get("RECAREA", [])]
            if not any(forest_name.lower() in n for n in names):
                continue
        score = _ratio(name, fac.get("FacilityName", ""))
        if score > best_score:
            best, best_score = fac, score

    return best if best_score >= FUZZ_THRESHOLD else None


def facility_campsite_count(session, facility_id):
    r = session.get(
        f"{BASE_URL}/facilities/{facility_id}/campsites",
        headers=headers(),
        params={"limit": 1, "offset": 0},
        timeout=30,
    )
    time.sleep(RATE_LIMIT_DELAY)
    if r.status_code != 200:
        return None
    return r.json().get("METADATA", {}).get("RESULTS", {}).get("TOTAL_COUNT")


UPSERT_CAMPSITE = """
    UPDATE campsites
    SET recreation_facility_id = %(fid)s,
        reservation_url        = %(url)s,
        num_sites              = COALESCE(%(num)s, num_sites),
        contact_phone          = COALESCE(%(phone)s, contact_phone),
        overview               = COALESCE(%(overview)s, overview),
        last_scraped           = now()
    WHERE id = %(id)s;
"""

UPSERT_RESERVATION = """
    INSERT INTO reservations
        (campsite_id, facility_id, provider, reservation_url, is_reservable, num_sites, last_checked)
    VALUES (%(id)s, %(fid)s, 'recreation_gov', %(url)s, %(reservable)s, %(num)s, now())
    ON CONFLICT (campsite_id) DO UPDATE SET
        facility_id     = EXCLUDED.facility_id,
        reservation_url = EXCLUDED.reservation_url,
        is_reservable   = EXCLUDED.is_reservable,
        num_sites       = EXCLUDED.num_sites,
        last_checked    = now();
"""

UPSERT_IMAGE = """
    INSERT INTO images (campsite_id, image_url, description)
    VALUES (%s, %s, %s)
    ON CONFLICT (campsite_id, image_url) DO NOTHING;
"""


def seed_agencies(conn, session):
    r = session.get(
        f"{BASE_URL}/organizations", headers=headers(),
        params={"limit": 200, "offset": 0}, timeout=30,
    )
    r.raise_for_status()
    orgs = r.json().get("RECDATA", [])
    cur = conn.cursor()
    for agency_name, hints in AGENCY_ORG_HINTS.items():
        match = next(
            (o for o in orgs
             if any(h in (o.get("OrgName", "").lower()) for h in hints)),
            None,
        )
        if not match:
            print(f"  no RIDB org for {agency_name!r}")
            continue
        org_id = int(match["OrgID"])
        cur.execute(
            "UPDATE agencies SET ridb_org_id = %s WHERE name = %s;", (org_id, agency_name)
        )
        print(f"  {agency_name} -> OrgID {org_id} ({match.get('OrgName')})")
    conn.commit()
    cur.close()


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--all", action="store_true", help="process every campsite, not just those missing a facility id")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--site-id", type=int, default=None)
    p.add_argument("--seed-agencies", action="store_true", help="fill agencies.ridb_org_id and exit")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    session = requests.Session()

    with get_conn() as conn:
        if args.seed_agencies:
            seed_agencies(conn, session)
            return

        sel = conn.cursor()
        if args.site_id is not None:
            sel.execute("SELECT id, name, forest_name FROM campsites WHERE id = %s;", (args.site_id,))
        else:
            q = "SELECT id, name, forest_name FROM campsites"
            if not args.all:
                q += " WHERE recreation_facility_id IS NULL"
            q += " ORDER BY id"
            if args.limit:
                q += f" LIMIT {int(args.limit)}"
            sel.execute(q)
        rows = sel.fetchall()
        sel.close()

        with scrape_run("ridb") as run:
            work = conn.cursor()
            for site_id, name, forest_name in rows:
                run.seen += 1
                try:
                    fac = search_facility(session, name, forest_name)
                    if not fac:
                        continue
                    fid = fac["FacilityID"]
                    num = facility_campsite_count(session, fid)
                    url = f"https://www.recreation.gov/camping/campgrounds/{fid}" if num else None

                    work.execute(UPSERT_CAMPSITE, {
                        "id": site_id, "fid": fid, "url": url, "num": num,
                        "phone": fac.get("FacilityPhone") or None,
                        "overview": clean_html(fac.get("FacilityDescription")),
                    })
                    work.execute(UPSERT_RESERVATION, {
                        "id": site_id, "fid": fid, "url": url,
                        "reservable": bool(fac.get("Reservable")), "num": num,
                    })
                    for media in fac.get("MEDIA", []):
                        if media.get("MediaType") == "Image" and media.get("URL"):
                            work.execute(UPSERT_IMAGE, (site_id, media["URL"], media.get("Title")))
                    conn.commit()
                    run.upserted += 1
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    run.errors += 1
                    print(f"  site {site_id} ({name}): {type(exc).__name__}: {exc}")
            work.close()


if __name__ == "__main__":
    main()
