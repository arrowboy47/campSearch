#!/usr/bin/env python3
"""Try to give coordinate-less campsites a real address-level lat/lon.

`backfill_approx_coords.py` already gives these a county-centroid *approximate*
point (distance + weather only). This job goes one better: geocode the site's
name / address via Nominatim and, if the result lands inside California, write
it to the real `latitude` / `longitude` columns so the site gets a map pin.

    python scripts/geocode_coordless.py            # geocode + write
    python scripts/geocode_coordless.py --dry-run
    python scripts/geocode_coordless.py --limit 20

Only rows with no real coordinates are touched, so it is safe to re-run: a site
that stays un-geocodable keeps its approximate point. After writing, it runs
`backfill_approx_coords` to clear the now-stale approx_* on anything it fixed.
"""

import re
import argparse

from _pipeline import get_conn, scrape_run
from geo import geocode, haversine_miles
import backfill_approx_coords

# generous California bounding box; anything outside is a bad geocode
CA_BOUNDS = (32.3, 42.1, -124.6, -114.0)  # min lat, max lat, min lon, max lon
# a campground sits inside its county; reject a geocode this far from the
# county centroid we already stored (covers even Inyo / San Bernardino)
MAX_MILES_FROM_COUNTY = 80

SELECT = """
    SELECT id, name, address, approx_latitude, approx_longitude
    FROM campsites
    WHERE (latitude IS NULL OR latitude = 'NaN'::numeric
           OR longitude IS NULL OR longitude = 'NaN'::numeric)
    ORDER BY id
"""

WRITE = """
    UPDATE campsites
    SET latitude = %(lat)s, longitude = %(lon)s, last_scraped = now()
    WHERE id = %(id)s
      AND (latitude IS NULL OR latitude = 'NaN'::numeric)
"""

_NOISE = re.compile(
    r"\b(campground|campgrounds|campsites|camp|group|site|area)\b", re.I
)
_COUNTY = re.compile(r"([A-Za-z][A-Za-z .'\-]+?)\s+County,\s*([A-Z]{2})\b")
_STREET = re.compile(r"^\s*(\d+\s+[A-Za-z0-9][A-Za-z0-9 .'\-]{3,40})")


def in_ca(lat, lon):
    return (CA_BOUNDS[0] <= lat <= CA_BOUNDS[1]
            and CA_BOUNDS[2] <= lon <= CA_BOUNDS[3])


def candidates(name, address):
    """Ordered geocode strings to try. Every candidate is anchored to the
    county (or the full verbose address) -- a bare name geocodes to the wrong
    same-named place too often."""
    cs = _COUNTY.search(address or "")
    if not cs:
        return [address] if address else []
    county = f"{cs.group(1)} County, {cs.group(2)}"
    bare_name = _NOISE.sub("", name or "").strip(" -")

    out = []
    if bare_name:
        out.append(f"{bare_name}, {county}")
    sm = _STREET.match(address or "")
    if sm:
        out.append(f"{sm.group(1)}, {county}")
    out.append(address)
    seen, uniq = set(), []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int)
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    with get_conn() as conn, scrape_run("geocode_coordless") as run:
        cur = conn.cursor()
        cur.execute(SELECT)
        rows = cur.fetchall()
        if args.limit:
            rows = rows[: args.limit]

        fixed = 0
        misses = []
        for cid, name, address, ax_lat, ax_lon in rows:
            run.seen += 1
            county_pt = ((float(ax_lat), float(ax_lon))
                         if ax_lat is not None and ax_lon is not None else None)
            hit = None
            used = None
            for q in candidates(name, address):
                pt = geocode(q)
                if not pt or not in_ca(pt[0], pt[1]):
                    continue
                if county_pt and haversine_miles(pt, county_pt) > MAX_MILES_FROM_COUNTY:
                    continue  # geocoded outside the site's own county -- reject
                hit, used = pt, q
                break
            if not hit:
                misses.append((cid, name))
                continue

            fixed += 1
            if args.dry_run:
                print(f"  [{cid}] {name[:38]:38} -> {hit[0]:.4f},{hit[1]:.4f}  via {used!r}")
            else:
                cur.execute(WRITE, {"id": cid, "lat": hit[0], "lon": hit[1]})
                run.upserted += 1
        if not args.dry_run:
            conn.commit()
        cur.close()

    print(f"\n{fixed} of {len(rows)} geocoded"
          f"{' (dry run)' if args.dry_run else ''}; {len(misses)} still coordless")
    for cid, name in misses[:40]:
        print(f"  miss  [{cid}] {name}")

    if not args.dry_run and fixed:
        print("\n--- clearing now-stale approx coords ---")
        backfill_approx_coords.main([])


if __name__ == "__main__":
    main()
