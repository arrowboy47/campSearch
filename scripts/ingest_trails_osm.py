#!/usr/bin/env python3
"""Nearby-trails data from OpenStreetMap via the Overpass API.

The AllTrails path (`ingest_alltrails.py`) needs the interactive MCP connector
one campsite at a time, so trail coverage stalled at a proof batch. OSM is
keyless, ODbL-licensed and scriptable, so this fills the bulk of it:

  * `route=hiking` relations  -> named trails, often with a `distance` tag and
    an `sac_scale` difficulty
  * `highway=path|footway` ways with a `name` -> shorter named trails / connectors

Both land in the shared `trails` / `campsite_trails` tables with `source='osm'`
(migration 0019). OSM ids are namespaced into `trails.id` so they can't collide
with AllTrails ids: relation -> 3e11 + id, way -> 2e11 + id.

Overpass is queried once per grid cell (default 0.15deg, ~16 km) rather than
once per campsite, then every trail is assigned to the campsites within
`--radius-miles` of it. This keeps it to a few hundred polite requests.

    python scripts/ingest_trails_osm.py                 # all non-dispersed sites
    python scripts/ingest_trails_osm.py --include-dispersed
    python scripts/ingest_trails_osm.py --campsite 733  # one site
    python scripts/ingest_trails_osm.py --dry-run --limit 5

It is idempotent (upsert on the namespaced id) and writes a `scrape_runs` row.
Because trails change slowly this is NOT in the daily chain; run it monthly.
"""

import time
import argparse
import collections

import requests

from _pipeline import get_conn, scrape_run
from geo import haversine_miles

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OSM_RELATION_OFFSET = 300_000_000_000
OSM_WAY_OFFSET = 200_000_000_000

# OSM sac_scale (hiking difficulty) -> our Easy/Moderate/Hard bucket
_SAC_DIFFICULTY = {
    "hiking": "Easy",
    "mountain_hiking": "Moderate",
    "demanding_mountain_hiking": "Hard",
    "alpine_hiking": "Hard",
    "demanding_alpine_hiking": "Hard",
    "difficult_alpine_hiking": "Hard",
}


def _grid_key(lat, lon, size):
    return (round(lat / size) * size, round(lon / size) * size)


def _length_miles(geometry):
    """Miles along an ordered list of {lat, lon} points (0 if too few)."""
    if not geometry or len(geometry) < 2:
        return None
    total = 0.0
    prev = None
    for pt in geometry:
        cur = (pt.get("lat"), pt.get("lon"))
        if cur[0] is None or cur[1] is None:
            prev = None
            continue
        if prev is not None:
            total += haversine_miles(prev, cur)
        prev = cur
    return round(total, 2) if total else None


def build_query(south, west, north, east):
    bbox = f"{south:.4f},{west:.4f},{north:.4f},{east:.4f}"
    return (
        "[out:json][timeout:90];"
        "("
        f'relation["route"="hiking"]["name"]({bbox});'
        f'way["highway"~"^(path|footway)$"]["name"]["foot"!~"^(no|private)$"]({bbox});'
        ");"
        "out tags geom;"
    )


def parse_element(el):
    """One Overpass element -> a trail dict, or None if unusable."""
    tags = el.get("tags") or {}
    name = tags.get("name")
    if not name:
        return None
    etype = el.get("type")
    if etype == "relation":
        tid = OSM_RELATION_OFFSET + el["id"]
        geom = []
        for member in el.get("members", []):
            geom.extend(member.get("geometry") or [])
    elif etype == "way":
        tid = OSM_WAY_OFFSET + el["id"]
        geom = el.get("geometry") or []
    else:
        return None

    # representative point for the campsite-distance test
    pts = [(p["lat"], p["lon"]) for p in geom if p.get("lat") is not None]
    if not pts:
        b = el.get("bounds")
        if not b:
            return None
        center = ((b["minlat"] + b["maxlat"]) / 2, (b["minlon"] + b["maxlon"]) / 2)
    else:
        center = pts[len(pts) // 2]

    # length: OSM `distance` tag (km) wins for relations, else sum the geometry
    length = None
    raw_dist = tags.get("distance")
    if raw_dist:
        try:
            length = round(float(raw_dist.split()[0].replace(",", ".")) * 0.621371, 2)
        except (ValueError, IndexError):
            length = None
    if length is None:
        length = _length_miles(geom)

    activities = ["hiking"]
    if tags.get("bicycle") in ("yes", "designated") or tags.get("mtb") == "yes":
        activities.append("biking")
    if tags.get("horse") in ("yes", "designated"):
        activities.append("horseback riding")

    return {
        "id": tid,
        "name": name[:200],
        "url": f"https://www.openstreetmap.org/{etype}/{el['id']}",
        "osm_type": etype,
        "difficulty": _SAC_DIFFICULTY.get(tags.get("sac_scale")),
        "route_type": "Loop" if tags.get("roundtrip") == "yes" else None,
        "length_miles": length,
        "activities": activities,
        "location_label": tags.get("operator") or tags.get("network"),
        "center": center,
    }


UPSERT_TRAIL = """
    INSERT INTO trails
        (id, name, url, source, osm_type, difficulty, route_type,
         length_miles, activities, location_label, last_scraped)
    VALUES
        (%(id)s, %(name)s, %(url)s, 'osm', %(osm_type)s, %(difficulty)s,
         %(route_type)s, %(length_miles)s, %(activities)s, %(location_label)s, now())
    ON CONFLICT (id) DO UPDATE SET
        name           = EXCLUDED.name,
        url            = EXCLUDED.url,
        osm_type       = EXCLUDED.osm_type,
        difficulty     = COALESCE(EXCLUDED.difficulty, trails.difficulty),
        route_type     = COALESCE(EXCLUDED.route_type, trails.route_type),
        length_miles   = COALESCE(EXCLUDED.length_miles, trails.length_miles),
        activities     = EXCLUDED.activities,
        location_label = COALESCE(EXCLUDED.location_label, trails.location_label),
        last_scraped   = now();
"""

UPSERT_LINK = """
    INSERT INTO campsite_trails (campsite_id, trail_id, distance_miles, last_seen)
    VALUES (%(cid)s, %(tid)s, %(dist)s, now())
    ON CONFLICT (campsite_id, trail_id) DO UPDATE SET
        distance_miles = LEAST(campsite_trails.distance_miles, EXCLUDED.distance_miles),
        last_seen      = now();
"""

# drop a campsite's previous OSM links before writing the fresh set, so a trail
# that was de-duplicated away (or moved out of range) doesn't linger
CLEAR_OSM_LINKS = """
    DELETE FROM campsite_trails
    WHERE campsite_id = %s
      AND trail_id IN (SELECT id FROM trails WHERE source = 'osm');
"""


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--radius-miles", type=float, default=6.0,
                   help="how close a trail must be to a campsite to link it")
    p.add_argument("--cell-size", type=float, default=0.15,
                   help="Overpass query grid size in degrees")
    p.add_argument("--per-campsite", type=int, default=12,
                   help="keep at most this many nearest trails per campsite")
    p.add_argument("--include-dispersed", action="store_true",
                   help="also do The Dyrt dispersed sites")
    p.add_argument("--campsite", type=int, help="only this campsite id")
    p.add_argument("--limit", type=int, help="stop after this many grid cells")
    p.add_argument("--sleep", type=float, default=1.0, help="pause between Overpass calls")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    session = requests.Session()
    session.headers.update({"User-Agent": "campsearch trail refresh (OSM/Overpass)"})

    with get_conn() as conn:
        sel = conn.cursor()
        q = ("SELECT id, latitude, longitude FROM campsites "
             "WHERE latitude IS NOT NULL AND longitude IS NOT NULL "
             "AND latitude <> 'NaN'::numeric AND longitude <> 'NaN'::numeric ")
        params = []
        if args.campsite:
            q += "AND id = %s"
            params.append(args.campsite)
        elif not args.include_dispersed:
            q += "AND source <> 'thedyrt'"
        sel.execute(q, params)
        sites = [(cid, float(lat), float(lon)) for cid, lat, lon in sel.fetchall()]
        sel.close()

    if not sites:
        print("no campsites match")
        return

    # bucket campsites into Overpass query cells
    cells = collections.defaultdict(list)
    for cid, lat, lon in sites:
        cells[_grid_key(lat, lon, args.cell_size)].append((cid, lat, lon))
    print(f"{len(sites)} campsites in {len(cells)} grid cells")

    margin = args.radius_miles / 55.0  # deg padding so edge sites see nearby trails
    half = args.cell_size / 2

    stats = {"trails": 0, "links": 0, "campsites": 0}

    def flush_campsite(cur, cid, hits):
        """Dedupe by name, keep the N nearest, replace this site's OSM links."""
        hits.sort(key=lambda x: x[0])
        keep, seen_names = [], set()
        for dist, t in hits:
            nm = t["name"].lower()
            if nm in seen_names:
                continue
            seen_names.add(nm)
            keep.append((dist, t))
            if len(keep) >= args.per_campsite:
                break
        if args.dry_run:
            stats["links"] += len(keep)
            if keep:
                stats["campsites"] += 1
            return
        cur.execute(CLEAR_OSM_LINKS, (cid,))
        for dist, t in keep:
            cur.execute(UPSERT_TRAIL, {k: t[k] for k in (
                "id", "name", "url", "osm_type", "difficulty",
                "route_type", "length_miles", "activities", "location_label")})
            cur.execute(UPSERT_LINK, {"cid": cid, "tid": t["id"], "dist": dist})
            stats["links"] += 1
        if keep:
            stats["campsites"] += 1

    write_conn = get_conn()
    write_cur = write_conn.cursor()
    trail_ids = set()

    with scrape_run("osm_trails") as run:
        for n, (key, members) in enumerate(sorted(cells.items()), 1):
            if args.limit and n > args.limit:
                break
            clat, clon = key
            query = build_query(clat - half - margin, clon - half - margin,
                                clat + half + margin, clon + half + margin)
            elements = None
            for attempt in range(3):
                try:
                    resp = session.post(OVERPASS_URL, data=query, timeout=180)
                    if resp.status_code in (429, 504):
                        time.sleep(15 * (attempt + 1))  # Overpass is busy — back off
                        continue
                    resp.raise_for_status()
                    elements = resp.json().get("elements", [])
                    break
                except Exception as exc:  # noqa: BLE001
                    if attempt == 2:
                        run.errors += 1
                        print(f"  ! cell {n}/{len(cells)} {key}: {type(exc).__name__}: {exc}")
                    else:
                        time.sleep(10)
            if elements is None:
                time.sleep(args.sleep)
                continue

            parsed = [t for t in (parse_element(el) for el in elements) if t]
            # OSM splits one trail into many same-named `way` segments. Where a
            # `route=hiking` relation exists for a name, drop the loose ways with
            # that name; otherwise keep only the longest way per name.
            rel_names = {t["name"].lower() for t in parsed if t["osm_type"] == "relation"}
            best_way = {}
            cell_trails = []
            for t in parsed:
                nm = t["name"].lower()
                if t["osm_type"] == "relation":
                    cell_trails.append(t)
                elif nm in rel_names:
                    continue
                else:
                    cur_best = best_way.get(nm)
                    if cur_best is None or (t["length_miles"] or 0) > (cur_best["length_miles"] or 0):
                        best_way[nm] = t
            cell_trails.extend(best_way.values())
            for t in cell_trails:
                trail_ids.add(t["id"])

            # this cell's campsites are complete now — write and move on, so a
            # long run persists incrementally instead of holding everything in RAM
            for cid, lat, lon in members:
                hits = [
                    (round(haversine_miles((lat, lon), t["center"]), 2), t)
                    for t in cell_trails
                    if haversine_miles((lat, lon), t["center"]) <= args.radius_miles
                ]
                if hits:
                    flush_campsite(write_cur, cid, hits)
            if not args.dry_run:
                write_conn.commit()

            run.seen += len(members)
            run.upserted = stats["links"]
            print(f"  cell {n}/{len(cells)} {key}: {len(elements)} elements, "
                  f"{len(cell_trails)} named trails")
            time.sleep(args.sleep)

    write_cur.close()
    write_conn.close()
    print(f"\n{'(dry run) ' if args.dry_run else ''}"
          f"{len(trail_ids)} distinct trails, {stats['links']} campsite links "
          f"across {stats['campsites']} campsites")


if __name__ == "__main__":
    main()
