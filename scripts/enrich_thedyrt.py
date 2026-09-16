#!/usr/bin/env python3
"""Detail-tier enrichment for The Dyrt campsites.

The list scrape (`scrape_thedyrt.py`) only sees the search-results payload, which
carries no amenities. The per-campground endpoint the website itself calls does:

    GET https://thedyrt.com/api/v6/campgrounds/<location-id>

`<location-id>` is the numeric `location-id` from the search-results attributes
(NOT the opaque JSON:API `id`). We rebuild the slug -> location-id map by
re-walking the same dispersed search, match it to our stored `site_url` slugs,
then for each campsite:

  * fill `campsites.overview` from `ai-description` / `description` (if empty)
  * fill `elevation_ft`, `seasons_of_use` (if empty)
  * map any populated boolean feature flags to `amenities.activities`,
    `water_feature`, `toilet_type`, `water`, `restrooms`
  * add `photo-urls` to `images`

Most dispersed pins only have the prose populated, so run `derive_attributes.py`
afterwards (run_all.py already chains it) to mine the freshly-stored overview.

    python scripts/enrich_thedyrt.py
    python scripts/enrich_thedyrt.py --limit 20 --dry-run

Verified 2026-09-07. If activity counts don't move, re-check the
`/api/v6/campgrounds/<id>` attribute keys.
"""

import argparse
import time

import requests

from _pipeline import get_conn, scrape_run
from scrape_thedyrt import iter_pins, CA_BBOX, HEADERS

DETAIL = "https://thedyrt.com/api/v6/campgrounds/{lid}"
RATE_LIMIT_DELAY = 0.4

# The Dyrt boolean flag -> our canonical activity tag (matches derive_attributes).
ACTIVITY_FLAGS = {
    "fishing": "fishing",
    "swimming": "swimming",
    "kayaking": "paddling",
    "hiking-trails": "hiking",
    "biking-trails": "biking",
    "equestrian-trails": "horseback riding",
    "horse-corral": "horseback riding",
    "boat-ramp": "boating",
    "boat-rental": "boating",
    "ohv-area": "off-roading",
    "skiing": "winter sports",
    "beach": "beach access",
}
# first truthy flag wins
WATER_FLAGS = [("lake", "lake"), ("river", "river"), ("ocean", "ocean")]


def _truthy(attrs, key):
    """Flag may be a bare bool at top level or {value, annotation} in
    annotated-features. None means 'unknown', not False."""
    v = attrs.get(key)
    if isinstance(v, dict):
        v = v.get("value")
    return v is True


def _known(attrs, key):
    v = attrs.get(key)
    if isinstance(v, dict):
        v = v.get("value")
    return v is not None


def derive_from_flags(attrs):
    af = attrs.get("annotated-features") or {}
    merged = {**af, **{k: attrs.get(k) for k in attrs}}  # top-level wins

    acts = sorted({tag for flag, tag in ACTIVITY_FLAGS.items() if _truthy(merged, flag)})

    water_feature = None
    for flag, name in WATER_FLAGS:
        if _truthy(merged, flag):
            water_feature = name
            break
    if water_feature is None and _truthy(merged, "beach"):
        water_feature = "ocean"

    toilet_type = None
    if _known(merged, "toilets"):
        if _truthy(merged, "toilets"):
            info = (attrs.get("toilet-info") or "").lower()
            if "flush" in info:
                toilet_type = "flush"
            elif "vault" in info or "pit" in info:
                toilet_type = "vault"
            else:
                toilet_type = "restrooms"
        else:
            toilet_type = "none"

    water = True if _truthy(merged, "drinking-water") else (
        False if _known(merged, "drinking-water") else None
    )
    restrooms = True if _truthy(merged, "toilets") else (
        False if _known(merged, "toilets") else None
    )
    return acts, water_feature, toilet_type, water, restrooms


def slug_of(site_url):
    return (site_url or "").rstrip("/").rsplit("/", 1)[-1] or None


def build_id_map(session, bbox):
    """slug -> numeric location-id, from a fresh dispersed search walk."""
    out = {}
    for attrs in iter_pins(session, bbox):
        slug = attrs.get("slug")
        lid = attrs.get("location-id")
        if slug and lid:
            out[slug] = lid
    return out


UPDATE_CAMPSITE = """
    UPDATE campsites SET
        overview        = COALESCE(overview, %(overview)s),
        elevation_ft    = COALESCE(elevation_ft, %(elev)s),
        seasons_of_use  = COALESCE(seasons_of_use, %(season)s),
        last_scraped    = now()
    WHERE id = %(id)s;
"""

UPSERT_IMAGE = """
    INSERT INTO images (campsite_id, image_url, description)
    VALUES (%s, %s, %s)
    ON CONFLICT (campsite_id, image_url) DO NOTHING;
"""


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bbox", default=CA_BBOX)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    session = requests.Session()

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT c.id, c.site_url, a.activities "
            "FROM campsites c LEFT JOIN amenities a ON a.campsite_id = c.id "
            "WHERE c.source = 'thedyrt' AND c.site_url IS NOT NULL;"
        )
        camp_rows = cur.fetchall()
        by_slug = {}
        for cid, url, acts in camp_rows:
            s = slug_of(url)
            if s:
                by_slug[s] = (cid, list(acts or []))
        cur.close()

        print(f"{len(by_slug)} thedyrt campsites; walking search for location ids...")
        id_map = build_id_map(session, args.bbox)
        print(f"{len(id_map)} slugs mapped to location ids")

        with scrape_run("enrich_thedyrt") as run:
            work = conn.cursor()
            done = 0
            for slug, (cid, existing_acts) in by_slug.items():
                lid = id_map.get(slug)
                if not lid:
                    continue
                run.seen += 1
                try:
                    r = session.get(DETAIL.format(lid=lid), headers=HEADERS, timeout=30)
                    if r.status_code == 404:
                        continue
                    r.raise_for_status()
                    attrs = r.json()["data"]["attributes"]
                except Exception as exc:  # noqa: BLE001
                    run.errors += 1
                    print(f"  ! {slug}: {type(exc).__name__}: {exc}")
                    continue

                acts, wfeat, toilet, water, restrooms = derive_from_flags(attrs)
                merged_acts = sorted(set(existing_acts) | set(acts))
                overview = attrs.get("ai-description") or attrs.get("description") or None
                try:
                    elev = round(float(attrs["elevation"])) if attrs.get("elevation") else None
                except (TypeError, ValueError):
                    elev = None  # The Dyrt sometimes sends "1234.5" or junk
                season = attrs.get("season") or None
                photos = attrs.get("photo-urls") or []

                if args.dry_run:
                    print(f"  {slug}: acts={merged_acts} water_feature={wfeat} "
                          f"toilet={toilet} water={water} elev={elev}")
                    done += 1
                    if args.limit and done >= args.limit:
                        break
                    time.sleep(RATE_LIMIT_DELAY)
                    continue

                try:
                    work.execute(UPDATE_CAMPSITE, {
                        "id": cid, "overview": overview,
                        "elev": elev, "season": season,
                    })
                    work.execute(
                        """
                        UPDATE amenities SET
                            activities    = %(acts)s,
                            water_feature = COALESCE(water_feature, %(wf)s),
                            toilet_type   = COALESCE(toilet_type, %(toilet)s),
                            water         = COALESCE(water, %(water)s),
                            restrooms     = COALESCE(restrooms, %(rest)s)
                        WHERE campsite_id = %(id)s;
                        """,
                        {"id": cid, "acts": merged_acts, "wf": wfeat,
                         "toilet": toilet, "water": water, "rest": restrooms},
                    )
                    for u in photos[:8]:
                        work.execute(UPSERT_IMAGE, (cid, u, None))
                    conn.commit()
                    run.upserted += 1
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    run.errors += 1
                    print(f"  ! {slug}: {type(exc).__name__}: {exc}")

                done += 1
                if args.limit and done >= args.limit:
                    break
                time.sleep(RATE_LIMIT_DELAY)
            work.close()

        print(f"done: seen={run.seen} updated={run.upserted} errors={run.errors}")


if __name__ == "__main__":
    main()
