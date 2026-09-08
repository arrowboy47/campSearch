#!/usr/bin/env python3
"""Load AllTrails "trails near a campground" data into trails / campsite_trails.

AllTrails has no open API — the only access is the claude.ai **AllTrails MCP
connector**, which only Claude can call, not a cron script. So this runs in two
halves:

  1. HARVEST (Claude, interactively). Get the target list:

         python scripts/ingest_alltrails.py --targets --limit 200 > targets.jsonl

     Then for each `{campsite_id, lat, lon}` line, Claude calls the MCP tool
     `find_trails_near_location(latitude, longitude, max_radius_meters=15000,
     limit=20, filters={activity:[hiking, backpacking]})` and appends one line to
     a harvest file:

         {"campsite_id": 123,
          "trails": [ <raw trail objects, verbatim from the tool> ],
          "details": { "<trail_id>": <raw get_trail_details .trail object> }}

     `details` is optional (fill it only for the few trails worth a description).

  2. LOAD (this script, offline). Upsert into the DB, idempotent, scrape_runs
     logged:

         python scripts/ingest_alltrails.py --load harvest.jsonl

Because the harvest is manual and trails change slowly, this is NOT in
run_all.py. Re-harvest a batch every few months (bump --offset).
"""

import re
import json
import argparse

from _pipeline import get_conn, scrape_run

UTM_RE = re.compile(r"[?&]utm_[^=]+=[^&]*")


def clean_url(url):
    if not url:
        return None
    url = UTM_RE.sub("", url)
    return url.rstrip("?&") or None


def _num(x):
    return x if isinstance(x, (int, float)) else None


# --- target list -------------------------------------------------------------

def emit_targets(args):
    with get_conn() as conn:
        cur = conn.cursor()
        q = (
            "SELECT id, latitude, longitude FROM campsites "
            "WHERE latitude IS NOT NULL AND longitude IS NOT NULL "
        )
        if not args.include_dispersed:
            q += "AND source <> 'thedyrt' "
        q += "ORDER BY id"
        if args.limit:
            q += f" LIMIT {int(args.limit)}"
        if args.offset:
            q += f" OFFSET {int(args.offset)}"
        cur.execute(q)
        for cid, lat, lon in cur.fetchall():
            print(json.dumps({"campsite_id": cid, "lat": float(lat), "lon": float(lon)}))
        cur.close()


# --- loader ----------------------------------------------------------------

UPSERT_TRAIL = """
    INSERT INTO trails
        (id, name, slug, url, difficulty, route_type, length_miles,
         elevation_gain_feet, elevation_max_feet, avg_rating, reviews_count,
         activities, features, location_label, description, review_summary,
         photo_url, last_scraped)
    VALUES
        (%(id)s, %(name)s, %(slug)s, %(url)s, %(difficulty)s, %(route_type)s,
         %(length_miles)s, %(elev_gain)s, %(elev_max)s, %(rating)s, %(reviews)s,
         %(activities)s, %(features)s, %(location_label)s, %(description)s,
         %(review_summary)s, %(photo)s, now())
    ON CONFLICT (id) DO UPDATE SET
        name                = EXCLUDED.name,
        slug                = COALESCE(EXCLUDED.slug, trails.slug),
        url                 = COALESCE(EXCLUDED.url, trails.url),
        difficulty          = COALESCE(EXCLUDED.difficulty, trails.difficulty),
        route_type          = COALESCE(EXCLUDED.route_type, trails.route_type),
        length_miles        = COALESCE(EXCLUDED.length_miles, trails.length_miles),
        elevation_gain_feet = COALESCE(EXCLUDED.elevation_gain_feet, trails.elevation_gain_feet),
        elevation_max_feet  = COALESCE(EXCLUDED.elevation_max_feet, trails.elevation_max_feet),
        avg_rating          = COALESCE(EXCLUDED.avg_rating, trails.avg_rating),
        reviews_count       = COALESCE(EXCLUDED.reviews_count, trails.reviews_count),
        activities          = CASE WHEN cardinality(EXCLUDED.activities) > 0
                                   THEN EXCLUDED.activities ELSE trails.activities END,
        features            = CASE WHEN cardinality(EXCLUDED.features) > 0
                                   THEN EXCLUDED.features ELSE trails.features END,
        location_label      = COALESCE(EXCLUDED.location_label, trails.location_label),
        description         = COALESCE(EXCLUDED.description, trails.description),
        review_summary      = COALESCE(EXCLUDED.review_summary, trails.review_summary),
        photo_url           = COALESCE(EXCLUDED.photo_url, trails.photo_url),
        last_scraped        = now();
"""

UPSERT_LINK = """
    INSERT INTO campsite_trails (campsite_id, trail_id, distance_miles, last_seen)
    VALUES (%(cid)s, %(tid)s, %(dist)s, now())
    ON CONFLICT (campsite_id, trail_id) DO UPDATE SET
        distance_miles = EXCLUDED.distance_miles,
        last_seen      = now();
"""


def trail_params(t, details):
    tid = t.get("id")
    d = (details or {}).get(str(tid)) or (details or {}).get(tid) or {}
    geo = d.get("trail_geo_stats", {}) if d else {}
    ucs = d.get("total_user_content_stats", {}) if d else {}
    return {
        "id": tid,
        "name": t.get("name") or (d.get("name") if d else None),
        "slug": (t.get("slug") or "").replace("trail/", "") or None,
        "url": clean_url(t.get("alltrails_web_url") or (d.get("alltrails_web_url") if d else None)),
        "difficulty": t.get("difficulty") or (d.get("difficulty") if d else None),
        "route_type": t.get("route_type") or (d.get("route_type") if d else None),
        "length_miles": _num(t.get("length_miles")) or _num(geo.get("length_miles")),
        "elev_gain": _num(t.get("elevation_gain_feet")) or _num(geo.get("elevation_gain_feet")),
        "elev_max": _num(geo.get("elevation_max_feet")),
        "rating": _num(t.get("avg_rating")) or _num(d.get("avg_rating") if d else None),
        "reviews": ucs.get("reviews_count"),
        "activities": [a.lower() for a in (t.get("activities") or [])],
        "features": [f.lower() for f in (t.get("features") or [])],
        "location_label": t.get("location_label") or (d.get("park", {}).get("name") if d else None),
        "description": (d.get("curated_description") or d.get("seo_overview")) if d else None,
        "review_summary": d.get("review_summary") if d else None,
        "photo": t.get("profile_photo_url") or (d.get("profile_photo_url") if d else None),
    }


def load_harvest(args):
    with get_conn() as conn, scrape_run("alltrails") as run:
        cur = conn.cursor()
        with open(args.load) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                cid = rec["campsite_id"]
                details = rec.get("details") or {}
                for t in rec.get("trails", []):
                    if not t.get("id"):
                        continue
                    run.seen += 1
                    try:
                        cur.execute(UPSERT_TRAIL, trail_params(t, details))
                        cur.execute(UPSERT_LINK, {
                            "cid": cid,
                            "tid": t["id"],
                            "dist": _num(t.get("trail_head_distance_miles")),
                        })
                        conn.commit()
                        run.upserted += 1
                    except Exception as exc:  # noqa: BLE001
                        conn.rollback()
                        run.errors += 1
                        print(f"  ! campsite {cid} trail {t.get('id')}: {type(exc).__name__}: {exc}")
        cur.close()


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--targets", action="store_true",
                   help="emit {campsite_id,lat,lon} JSONL for the harvest step")
    g.add_argument("--load", metavar="FILE", help="load a harvest JSONL file")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--offset", type=int, default=None)
    p.add_argument("--include-dispersed", action="store_true",
                   help="targets: also include The Dyrt dispersed sites")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.targets:
        emit_targets(args)
    else:
        load_harvest(args)


if __name__ == "__main__":
    main()
