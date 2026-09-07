#!/usr/bin/env python3
"""Ingest dispersed / free camping sites from The Dyrt.

fs.usda / RIDB / ReserveCalifornia only know about *established* campgrounds.
Dispersed camping (no facilities, no reservation, often just a pin on BLM or
Forest Service land) is the gap. The Dyrt is the largest crowd-sourced source
for it.

Undocumented but stable JSON:API endpoint the website itself calls:

    GET https://thedyrt.com/api/v6/locations/search-results
        ?filter[search][bbox]=<W,S,E,N>
        &filter[search][pin_type]=dispersed
        &page[number]=N&page[size]=50

`page[size]` is capped at 50 server-side; page through `meta.page-count`. The
bbox for California also drags in NV/AZ border pins, so we keep only
`region-name == "California"` unless --all-regions.

    python scripts/scrape_thedyrt.py
    python scripts/scrape_thedyrt.py --dry-run --limit 20
    python scripts/scrape_thedyrt.py --bbox -120.5,37.5,-118.5,39.5   # Tahoe-ish

Idempotent: upsert on site_url (the campground's public page), one row per pin.
Selectors / param names verified 2026-09-07; if row counts crater, re-check
`filter[search][pin_type]` and the `data[].attributes` keys.
"""

import time
import argparse

import requests

from _pipeline import get_conn, scrape_run

ENDPOINT = "https://thedyrt.com/api/v6/locations/search-results"
# Whole state, generous — trims NV/AZ spill by region-name afterwards.
CA_BBOX = "-124.6,32.4,-114.0,42.1"
PAGE_SIZE = 50
MAX_PAGES = 80          # safety valve; CA dispersed is ~600 pins => ~13 pages
RATE_LIMIT_DELAY = 0.5

HEADERS = {
    "User-Agent": "campsearch data refresh (github arrowboy47)",
    "Accept": "application/json",
}

# operator string on the pin -> agencies.name
AGENCY_HINTS = [
    ("forest service", "US Forest Service"),
    ("national park", "National Park Service"),
    ("bureau of land management", "Bureau of Land Management"),
    ("blm", "Bureau of Land Management"),
    ("state park", "California State Parks"),
]


def load_agency_map(conn):
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM agencies;")
    by_name = {name.lower(): aid for aid, name in cur.fetchall()}
    cur.close()
    return by_name


def agency_for(operator, by_name):
    if not operator:
        return None
    op = operator.lower()
    for hint, canonical in AGENCY_HINTS:
        if hint in op and canonical.lower() in by_name:
            return by_name[canonical.lower()]
    return None


def iter_pins(session, bbox):
    """Yield attribute dicts for every dispersed pin in the bbox."""
    page = 1
    while page <= MAX_PAGES:
        params = {
            "filter[search][bbox]": bbox,
            "filter[search][pin_type]": "dispersed",
            "sort": "recommended",
            "page[number]": page,
            "page[size]": PAGE_SIZE,
        }
        r = session.get(ENDPOINT, headers=HEADERS, params=params, timeout=45)
        r.raise_for_status()
        body = r.json()
        rows = body.get("data", [])
        if not rows:
            break
        for row in rows:
            attrs = row.get("attributes", {})
            attrs["_id"] = row.get("id")
            yield attrs
        page_count = body.get("meta", {}).get("page-count", 0)
        if page >= page_count:
            break
        page += 1
        time.sleep(RATE_LIMIT_DELAY)


def region_slug(region_name):
    return (region_name or "").strip().lower().replace(" ", "-") or "unknown"


def fee_text(low_cents, high_cents):
    if not low_cents:
        return "Free"
    lo = low_cents / 100
    hi = (high_cents or low_cents) / 100
    return f"${lo:.0f}" if lo == hi else f"${lo:.0f}–${hi:.0f}"


UPSERT_CAMPSITE = """
    INSERT INTO campsites
        (name, latitude, longitude, managing_unit, reservation_type,
         site_url, agency_id, fee, primary_image_url, source,
         first_seen, last_scraped)
    VALUES
        (%(name)s, %(lat)s, %(lon)s, %(unit)s, 'dispersed',
         %(url)s, %(agency_id)s, %(fee)s, %(photo)s, 'thedyrt',
         now(), now())
    ON CONFLICT (site_url) WHERE site_url IS NOT NULL
    DO UPDATE SET
        name             = EXCLUDED.name,
        latitude         = COALESCE(EXCLUDED.latitude, campsites.latitude),
        longitude        = COALESCE(EXCLUDED.longitude, campsites.longitude),
        managing_unit    = COALESCE(EXCLUDED.managing_unit, campsites.managing_unit),
        reservation_type = 'dispersed',
        agency_id        = COALESCE(EXCLUDED.agency_id, campsites.agency_id),
        fee              = COALESCE(EXCLUDED.fee, campsites.fee),
        primary_image_url = COALESCE(EXCLUDED.primary_image_url, campsites.primary_image_url),
        last_scraped     = now()
    RETURNING id, (xmax = 0) AS inserted;
"""

UPSERT_IMAGE = """
    INSERT INTO images (campsite_id, image_url, description)
    VALUES (%s, %s, %s)
    ON CONFLICT (campsite_id, image_url) DO NOTHING;
"""


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bbox", default=CA_BBOX, help="W,S,E,N (default: all of California)")
    p.add_argument("--region", default="California",
                   help="keep only this region-name (default California)")
    p.add_argument("--all-regions", action="store_true",
                   help="do not filter by region-name")
    p.add_argument("--limit", type=int, default=None, help="stop after N usable pins")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    session = requests.Session()
    want_region = None if args.all_regions else args.region

    with get_conn() as conn:
        by_name = load_agency_map(conn)

        with scrape_run("thedyrt") as run:
            work = conn.cursor()
            done = 0
            for attrs in iter_pins(session, args.bbox):
                region = attrs.get("region-name")
                if want_region and region != want_region:
                    continue
                lat = attrs.get("latitude")
                lon = attrs.get("longitude")
                slug = attrs.get("slug")
                if not slug or lat is None or lon is None:
                    continue

                run.seen += 1
                url = f"https://thedyrt.com/camping/{region_slug(region)}/{slug}"
                params = {
                    "name": attrs.get("name"),
                    "lat": float(lat),
                    "lon": float(lon),
                    "unit": attrs.get("administrative-area") or None,
                    "url": url,
                    "agency_id": agency_for(attrs.get("operator"), by_name),
                    "fee": fee_text(attrs.get("price-low-cents"), attrs.get("price-high-cents")),
                    "photo": attrs.get("photo-url") or None,
                }

                if args.dry_run:
                    print(f"  + {params['name']}  ({lat},{lon})  {params['fee']}  {url}")
                    done += 1
                    if args.limit and done >= args.limit:
                        break
                    continue

                try:
                    work.execute(UPSERT_CAMPSITE, params)
                    camp_id, inserted = work.fetchone()
                    if params["photo"]:
                        work.execute(UPSERT_IMAGE, (camp_id, params["photo"], attrs.get("name")))
                    conn.commit()
                    run.upserted += 1
                    if inserted:
                        print(f"  + {params['name']}")
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    run.errors += 1
                    print(f"  ! {params['name']}: {type(exc).__name__}: {exc}")

                done += 1
                if args.limit and done >= args.limit:
                    break
            work.close()


if __name__ == "__main__":
    main()
