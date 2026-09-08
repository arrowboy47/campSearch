#!/usr/bin/env python3
"""Ingest campground facilities operated by an org straight from RIDB.

sync_ridb.py only *matches* recreation.gov facilities against campsites we
already scraped from fs.usda (i.e. Forest Service land). This script goes the
other way: it enumerates every Campground facility under a given organization
(National Park Service, BLM, ...) in a state and INSERTs the ones we don't have.
That's how NPS / BLM / state-park campgrounds enter the DB at all.

    python scripts/ingest_ridb_orgs.py --org NPS --state CA
    python scripts/ingest_ridb_orgs.py --org all --state CA --limit 50
    python scripts/ingest_ridb_orgs.py --org BLM --state CA --dry-run

Needs RIDB_API_KEY. Idempotent: a facility we already carry (by
recreation_facility_id) is skipped; re-inserted rows conflict on site_url and
refresh instead of duplicating.
"""

import time
import argparse

import requests

from _pipeline import get_conn, scrape_run
from sync_ridb import headers, clean_html  # reuse (headers() reads the key)

BASE_URL = "https://ridb.recreation.gov/api/v1"
RATE_LIMIT_DELAY = 0.4
PAGE = 50


def iter_org_facilities(session, org_id):
    """Yield every facility dict under an org, paging through RIDB."""
    offset = 0
    while True:
        r = session.get(
            f"{BASE_URL}/organizations/{org_id}/facilities",
            headers=headers(),
            params={"limit": PAGE, "offset": offset, "full": "true"},
            timeout=45,
        )
        time.sleep(RATE_LIMIT_DELAY)
        r.raise_for_status()
        body = r.json()
        recs = body.get("RECDATA", [])
        for fac in recs:
            yield fac
        total = body.get("METADATA", {}).get("RESULTS", {}).get("TOTAL_COUNT", 0)
        offset += PAGE
        if offset >= total or not recs:
            break


def facility_state(fac):
    for addr in fac.get("FACILITYADDRESS", []) or []:
        code = (addr.get("AddressStateCode") or "").upper()
        if code:
            return code
    return None


def is_campground(fac):
    return (fac.get("FacilityTypeDescription") or "").lower() == "campground"


def usable(fac, want_state):
    if not is_campground(fac):
        return False
    if not fac.get("FacilityLatitude") or not fac.get("FacilityLongitude"):
        return False
    if want_state is None:
        return True
    return facility_state(fac) == want_state


INSERT_CAMPSITE = """
    INSERT INTO campsites
        (name, latitude, longitude, address, managing_unit, reservation_type,
         reservation_url, overview, site_url, recreation_facility_id, agency_id,
         num_sites, contact_phone, source, first_seen, last_scraped)
    VALUES
        (%(name)s, %(lat)s, %(lon)s, %(address)s, %(unit)s, %(rtype)s,
         %(rurl)s, %(overview)s, %(site_url)s, %(fid)s, %(agency_id)s,
         %(num)s, %(phone)s, 'ridb', now(), now())
    ON CONFLICT (site_url) WHERE site_url IS NOT NULL
    DO UPDATE SET
        name          = EXCLUDED.name,
        latitude      = EXCLUDED.latitude,
        longitude     = EXCLUDED.longitude,
        managing_unit = COALESCE(EXCLUDED.managing_unit, campsites.managing_unit),
        reservation_url = EXCLUDED.reservation_url,
        overview      = COALESCE(EXCLUDED.overview, campsites.overview),
        agency_id     = EXCLUDED.agency_id,
        last_scraped  = now()
    RETURNING id, (xmax = 0) AS inserted;
"""

UPSERT_RESERVATION = """
    INSERT INTO reservations
        (campsite_id, facility_id, provider, reservation_url, is_reservable, num_sites, last_checked)
    VALUES (%(id)s, %(fid)s, 'recreation_gov', %(rurl)s, %(reservable)s, %(num)s, now())
    ON CONFLICT (campsite_id) DO UPDATE SET
        facility_id = EXCLUDED.facility_id,
        reservation_url = EXCLUDED.reservation_url,
        is_reservable = EXCLUDED.is_reservable,
        num_sites = EXCLUDED.num_sites,
        last_checked = now();
"""

UPSERT_IMAGE = """
    INSERT INTO images (campsite_id, image_url, description)
    VALUES (%s, %s, %s)
    ON CONFLICT (campsite_id, image_url) DO NOTHING;
"""


def load_orgs(conn, which):
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, ridb_org_id FROM agencies "
        "WHERE ridb_org_id IS NOT NULL ORDER BY id;"
    )
    rows = cur.fetchall()
    cur.close()
    by_short = {
        "USFS": "US Forest Service",
        "NPS": "National Park Service",
        "BLM": "Bureau of Land Management",
    }
    if which == "all":
        return rows
    target = by_short.get(which, which)
    return [r for r in rows if r[1] == target]


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", default="NPS",
                   help="NPS | BLM | USFS | all | exact agencies.name")
    p.add_argument("--state", default="CA", help="2-letter state filter, or 'any'")
    p.add_argument("--limit", type=int, default=None, help="stop after N usable facilities")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    want_state = None if args.state.lower() == "any" else args.state.upper()
    session = requests.Session()

    with get_conn() as conn:
        orgs = load_orgs(conn, args.org)
        if not orgs:
            raise SystemExit(f"no seeded agency matches --org {args.org!r} "
                             "(run sync_ridb.py --seed-agencies first)")

        with scrape_run("ridb_orgs") as run:
            work = conn.cursor()
            have = conn.cursor()
            n_usable = 0
            for agency_id, agency_name, org_id in orgs:
                print(f"== {agency_name} (OrgID {org_id}) ==")
                for fac in iter_org_facilities(session, org_id):
                    if not usable(fac, want_state):
                        continue
                    if args.limit and n_usable >= args.limit:
                        break
                    n_usable += 1
                    run.seen += 1
                    fid = str(fac["FacilityID"])

                    have.execute(
                        "SELECT id FROM campsites WHERE recreation_facility_id = %s LIMIT 1;",
                        (fid,),
                    )
                    if have.fetchone():
                        continue  # already covered (fs.usda + sync_ridb)

                    recareas = fac.get("RECAREA") or []
                    unit = recareas[0].get("RecAreaName") if recareas else None
                    reservable = bool(fac.get("Reservable"))
                    rurl = (f"https://www.recreation.gov/camping/campgrounds/{fid}"
                            if reservable else None)
                    site_url = rurl or fac.get("FacilityMapURL") or f"ridb://facility/{fid}"
                    addr = None
                    for a in fac.get("FACILITYADDRESS", []) or []:
                        addr = ", ".join(x for x in [
                            a.get("FacilityStreetAddress1"), a.get("City"),
                            a.get("AddressStateCode"), a.get("PostalCode"),
                        ] if x) or None
                        break

                    if args.dry_run:
                        print(f"  + {fac.get('FacilityName')}  ({fid})  {unit}")
                        continue

                    params = {
                        "name": fac.get("FacilityName"),
                        "lat": float(fac["FacilityLatitude"]),
                        "lon": float(fac["FacilityLongitude"]),
                        "address": addr,
                        "unit": unit,
                        "rtype": "reservation" if reservable else "first-come",
                        "rurl": rurl,
                        "overview": clean_html(fac.get("FacilityDescription")),
                        "site_url": site_url,
                        "fid": fid,
                        "agency_id": agency_id,
                        "num": None,
                        "phone": fac.get("FacilityPhone") or None,
                    }
                    try:
                        work.execute(INSERT_CAMPSITE, params)
                        camp_id, inserted = work.fetchone()
                        work.execute(UPSERT_RESERVATION, {
                            "id": camp_id, "fid": fid, "rurl": rurl,
                            "reservable": reservable, "num": None,
                        })
                        for m in fac.get("MEDIA", []) or []:
                            if m.get("MediaType") == "Image" and m.get("URL"):
                                work.execute(UPSERT_IMAGE, (camp_id, m["URL"], m.get("Title")))
                        conn.commit()
                        run.upserted += 1
                        if inserted:
                            print(f"  + {params['name']} ({fid})")
                    except Exception as exc:  # noqa: BLE001
                        conn.rollback()
                        run.errors += 1
                        print(f"  ! {params['name']} ({fid}): {type(exc).__name__}: {exc}")
                if args.limit and n_usable >= args.limit:
                    break
            work.close()
            have.close()


if __name__ == "__main__":
    main()
