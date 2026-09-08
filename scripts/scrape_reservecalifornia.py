#!/usr/bin/env python3
"""Ingest California State Parks campgrounds from ReserveCalifornia (UseDirect).

ReserveCalifornia's backend moved off usedirect.com to a Tyler Technologies host.
Two open JSON endpoints, no key:

    {BASE}/rdr/fd/places      -> 299 parks: PlaceId, Name, address, lat/lon, phone
    {BASE}/rdr/fd/facilities  -> 517 bookable camping facilities: FacilityId, PlaceId

Facilities carry no coordinates, so we join each to its parent place. Every RC
facility is a camping unit (day-use is a separate system), so we take them all,
recording is_reservable = AllowWebBooking.

    python scripts/scrape_reservecalifornia.py
    python scripts/scrape_reservecalifornia.py --dry-run --limit 20

Idempotent: upsert on site_url (the booking URL), one reservations row per site.
"""

import argparse

import requests

from _pipeline import get_conn, scrape_run

BASE = "https://california-rdr.prod.cali.rd12.recreation-management.tylerapp.com"
BOOKING = "https://www.reservecalifornia.com/park/{place_id}/{facility_id}"


def fetch_json(session, path):
    r = session.get(f"{BASE}/{path}", timeout=90)
    r.raise_for_status()
    return r.json()


UPSERT_CAMPSITE = """
    INSERT INTO campsites
        (name, latitude, longitude, address, managing_unit, reservation_type,
         reservation_url, site_url, agency_id, contact_phone, source,
         first_seen, last_scraped)
    VALUES
        (%(name)s, %(lat)s, %(lon)s, %(address)s, %(unit)s, %(rtype)s,
         %(url)s, %(url)s, %(agency_id)s, %(phone)s, 'reserve_california',
         now(), now())
    ON CONFLICT (site_url) WHERE site_url IS NOT NULL
    DO UPDATE SET
        name          = EXCLUDED.name,
        latitude      = COALESCE(EXCLUDED.latitude, campsites.latitude),
        longitude     = COALESCE(EXCLUDED.longitude, campsites.longitude),
        managing_unit = EXCLUDED.managing_unit,
        reservation_url = EXCLUDED.reservation_url,
        contact_phone = COALESCE(campsites.contact_phone, EXCLUDED.contact_phone),
        agency_id     = EXCLUDED.agency_id,
        last_scraped  = now()
    RETURNING id, (xmax = 0) AS inserted;
"""

UPSERT_RESERVATION = """
    INSERT INTO reservations
        (campsite_id, facility_id, provider, reservation_url, is_reservable, last_checked)
    VALUES (%(id)s, %(fid)s, 'reserve_california', %(url)s, %(reservable)s, now())
    ON CONFLICT (campsite_id) DO UPDATE SET
        facility_id     = EXCLUDED.facility_id,
        provider        = EXCLUDED.provider,
        reservation_url = EXCLUDED.reservation_url,
        is_reservable   = EXCLUDED.is_reservable,
        last_checked    = now();
"""


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--bookable-only", action="store_true",
                   help="skip facilities with AllowWebBooking = false")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    session = requests.Session()
    session.headers.update({"User-Agent": "campsearch data refresh", "Accept": "application/json"})

    with get_conn() as conn:
        ag = conn.cursor()
        ag.execute("SELECT id FROM agencies WHERE name = 'California State Parks';")
        row = ag.fetchone()
        ag.close()
        if not row:
            raise SystemExit("agencies row 'California State Parks' missing")
        agency_id = row[0]

        places = {p["PlaceId"]: p for p in fetch_json(session, "rdr/fd/places")}
        facilities = fetch_json(session, "rdr/fd/facilities")
        print(f"{len(places)} places, {len(facilities)} facilities")

        with scrape_run("reserve_california") as run:
            work = conn.cursor()
            done = 0
            for fac in facilities:
                place = places.get(fac.get("PlaceId"))
                if not place:
                    continue
                reservable = bool(fac.get("AllowWebBooking"))
                if args.bookable_only and not reservable:
                    continue

                run.seen += 1
                fid = fac["FacilityId"]
                pid = fac["PlaceId"]
                url = BOOKING.format(place_id=pid, facility_id=fid)
                lat = place.get("Latitude")
                lon = place.get("Longitude")
                address = ", ".join(
                    str(x) for x in [place.get("Address1"), place.get("City"),
                                     place.get("State"), place.get("Zip")] if x
                ) or None

                if args.dry_run:
                    print(f"  + {fac['Name']}  <-  {place['Name']}  ({lat},{lon})  book={reservable}")
                    done += 1
                    if args.limit and done >= args.limit:
                        break
                    continue

                params = {
                    "name": fac["Name"],
                    "lat": float(lat) if lat not in (None, 0) else None,
                    "lon": float(lon) if lon not in (None, 0) else None,
                    "address": address,
                    "unit": place["Name"],
                    "rtype": "reservation" if reservable else "first-come",
                    "url": url,
                    "agency_id": agency_id,
                    "phone": place.get("VoicePhone") or None,
                }
                try:
                    work.execute(UPSERT_CAMPSITE, params)
                    camp_id, inserted = work.fetchone()
                    work.execute(UPSERT_RESERVATION, {
                        "id": camp_id, "fid": str(fid), "url": url, "reservable": reservable,
                    })
                    conn.commit()
                    run.upserted += 1
                    if inserted:
                        print(f"  + {fac['Name']} ({place['Name']})")
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    run.errors += 1
                    print(f"  ! {fac['Name']}: {type(exc).__name__}: {exc}")

                done += 1
                if args.limit and done >= args.limit:
                    break
            work.close()


if __name__ == "__main__":
    main()
