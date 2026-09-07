#!/usr/bin/env python3
"""Fill campsites.elevation_ft from a DEM lookup, then derive campsites.terrain.

Elevation comes from Open-Meteo's elevation API (no key, up to 100 coords per
request, ~90 m SRTM resolution). Only rows with a NULL elevation_ft are fetched,
so re-runs just pick up newly scraped sites. `terrain` is a cheap deterministic
band and is recomputed for every row each run.

    python scripts/backfill_elevation.py
    python scripts/backfill_elevation.py --refresh   # refetch every elevation
    python scripts/backfill_elevation.py --limit 200

terrain bands: high desert / coastal / alpine / subalpine forest /
montane forest / foothills / valley
"""

import time
import argparse

import requests

from _pipeline import get_conn, scrape_run

ELEV_URL = "https://api.open-meteo.com/v1/elevation"
BATCH = 100
SLEEP = 3.0          # open-meteo free tier is rate-limited; go easy
M_TO_FT = 3.28084


def fetch_elevations(session, coords, retries=5):
    """coords: list of (lat, lon) -> list of elevation_ft (int) aligned to input."""
    lats = ",".join(f"{lat:.5f}" for lat, _ in coords)
    lons = ",".join(f"{lon:.5f}" for _, lon in coords)
    backoff = 5.0
    for attempt in range(retries):
        r = session.get(ELEV_URL, params={"latitude": lats, "longitude": lons}, timeout=45)
        if r.status_code == 429:
            time.sleep(backoff)
            backoff *= 2
            continue
        r.raise_for_status()
        out = r.json().get("elevation") or []
        return [round(m * M_TO_FT) if m is not None else None for m in out]
    r.raise_for_status()  # exhausted retries, surface the 429


def terrain_band(elev_ft, lat, lon):
    """Coarse landscape label. Deliberately rough — enough for a search facet."""
    lat = float(lat)
    lon = float(lon)

    # Terrain needs elevation; leave NULL so a later run classifies it.
    if elev_ft is None:
        return None

    # SE California deserts (Mojave / Colorado / Death Valley area). Kept tight so
    # the southern Sierra and San Bernardino peaks don't get mislabelled.
    if lon > -117.3 and lat < 36.6 and elev_ft < 4500:
        return "high desert"

    # Near the coast and low: the Coast Ranges / coastal terraces.
    if lon < -120.5 and lat < 40.8 and elev_ft < 700:
        return "coastal"
    if elev_ft >= 9500:
        return "alpine"
    if elev_ft >= 7000:
        return "subalpine forest"
    if elev_ft >= 4000:
        return "montane forest"
    if elev_ft >= 1500:
        return "foothills"
    return "valley"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--refresh", action="store_true", help="refetch elevation for every site")
    p.add_argument("--limit", type=int, default=None)
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    session = requests.Session()

    with get_conn() as conn, scrape_run("elevation") as run:
        sel = conn.cursor()
        q = ("SELECT id, latitude, longitude FROM campsites "
             "WHERE latitude IS NOT NULL AND longitude IS NOT NULL "
             "AND latitude <> 'NaN'::numeric AND longitude <> 'NaN'::numeric")
        if not args.refresh:
            q += " AND elevation_ft IS NULL"
        q += " ORDER BY id"
        if args.limit:
            q += f" LIMIT {int(args.limit)}"
        sel.execute(q)
        todo = sel.fetchall()
        sel.close()

        upd = conn.cursor()

        # 1. elevation, in batches
        for i in range(0, len(todo), BATCH):
            chunk = todo[i:i + BATCH]
            coords = [(float(lat), float(lon)) for _, lat, lon in chunk]
            try:
                elevs = fetch_elevations(session, coords)
            except Exception as exc:  # noqa: BLE001
                run.errors += 1
                print(f"  ! batch {i}: {type(exc).__name__}: {exc}")
                continue
            for (cid, _, _), ft in zip(chunk, elevs):
                run.seen += 1
                if ft is None:
                    continue
                upd.execute("UPDATE campsites SET elevation_ft = %s WHERE id = %s", (ft, cid))
                run.upserted += 1
            conn.commit()
            time.sleep(SLEEP)

        # 2. terrain for everyone (deterministic, cheap)
        sel = conn.cursor()
        sel.execute("SELECT id, elevation_ft, latitude, longitude FROM campsites WHERE latitude IS NOT NULL")
        rows = sel.fetchall()
        sel.close()
        for cid, elev, lat, lon in rows:
            band = terrain_band(elev, lat, lon)
            upd.execute("UPDATE campsites SET terrain = %s WHERE id = %s", (band, cid))
        conn.commit()
        upd.close()


if __name__ == "__main__":
    main()
