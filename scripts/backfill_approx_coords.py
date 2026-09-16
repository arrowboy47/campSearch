#!/usr/bin/env python3
"""Give coordinate-less campsites an APPROXIMATE county-level location.

Some scraped campgrounds (mostly county / regional parks from
californiasbestcamping.com) publish no lat/lon. Their `address` still ends in
``..., <County> County, <ST>``, so we look that county up in the vendored
Census centroid table (`data/county_centroids.tsv`) and store its internal
point in `approx_latitude` / `approx_longitude` (migration 0020).

These columns are consumed only for a rough distance estimate and a weather
forecast, always flagged "approximate" in the UI, and never used to place a map
marker.

    python scripts/backfill_approx_coords.py            # fill + tidy
    python scripts/backfill_approx_coords.py --dry-run

Idempotent and self-correcting:
  * a row that already has real latitude/longitude has its approx_* cleared
  * a row whose county isn't in the table is left with approx_* NULL and listed
  * (0, 0) is never written
"""

import argparse

from _pipeline import get_conn, scrape_run
from geo import parse_county_state, county_centroid

SELECT_CANDIDATES = """
    SELECT id, name, address, source
    FROM campsites
    WHERE (latitude IS NULL OR latitude = 'NaN'::numeric
           OR longitude IS NULL OR longitude = 'NaN'::numeric)
      AND approx_latitude IS NULL
"""

# a row that has since gained real coordinates should not keep a stale approx
CLEAR_STALE = """
    UPDATE campsites
    SET approx_latitude = NULL, approx_longitude = NULL, approx_coord_source = NULL
    WHERE approx_latitude IS NOT NULL
      AND latitude IS NOT NULL AND latitude <> 'NaN'::numeric
      AND longitude IS NOT NULL AND longitude <> 'NaN'::numeric
"""

WRITE = """
    UPDATE campsites
    SET approx_latitude = %(lat)s, approx_longitude = %(lon)s,
        approx_coord_source = %(src)s
    WHERE id = %(id)s
"""


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    with get_conn() as conn, scrape_run("approx_coords") as run:
        cur = conn.cursor()

        cur.execute(CLEAR_STALE)
        cleared = cur.rowcount
        if not args.dry_run:
            conn.commit()

        cur.execute(SELECT_CANDIDATES)
        rows = cur.fetchall()

        filled = 0
        no_county = []
        no_centroid = []
        for cid, name, address, source in rows:
            run.seen += 1
            cs = parse_county_state(address)
            if not cs:
                no_county.append((cid, name, source))
                continue
            county, state = cs
            pt = county_centroid(state, county)
            if not pt or pt == (0.0, 0.0):
                no_centroid.append((cid, name, f"{county} County, {state}"))
                continue
            src = f"county-centroid:{state}/{county}"
            if args.dry_run:
                print(f"  [{cid}] {name[:40]:40} -> {county} County, {state}  {pt}")
            else:
                cur.execute(WRITE, {"id": cid, "lat": pt[0], "lon": pt[1], "src": src})
                run.upserted += 1
            filled += 1

        if not args.dry_run:
            conn.commit()
        cur.close()

    print(f"\ncleared stale approx on {cleared} row(s) that now have real coords")
    print(f"{filled} coordless campsite(s) given a county-centroid approx"
          f"{' (dry run, not written)' if args.dry_run else ''}")
    if no_county:
        print(f"\n{len(no_county)} had no parseable county in `address` (left as-is):")
        for cid, name, src in no_county[:30]:
            print(f"  [{cid}] {name}  ({src})")
    if no_centroid:
        print(f"\n{len(no_centroid)} named a county not in the centroid table (left as-is):")
        for cid, name, cc in no_centroid[:30]:
            print(f"  [{cid}] {name}  -> {cc}")


if __name__ == "__main__":
    main()
