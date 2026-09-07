import psycopg2
from psycopg2.extras import RealDictCursor

from config import database_url


def get_connection():
    """Open a new Postgres connection from DATABASE_URL.

    Was hardcoded to a local unix socket (dbname=camping user=arrowboy); now
    points wherever DATABASE_URL says — a local dev container, or campsearch-pg
    on thebigbox over an SSH tunnel.
    """
    return psycopg2.connect(database_url())


# Full campsite detail: the campsites row plus the one-to-one satellites the
# detail page needs (agency name, amenities flags, open/closed, booking info).
# reservations / amenities / status_updates each have UNIQUE (campsite_id), so
# these LEFT JOINs stay one row.
CAMPSITE_SQL = """
    SELECT
        c.id, c.name,
        c.latitude, c.longitude,
        c.address, c.managing_unit, c.forest_name,
        c.reservation_type, c.reservation_url,
        c.contact_name, c.contact_phone,
        c.seasons_of_use, c.num_sites, c.fee, c.overview,
        c.site_url, c.source, c.primary_image_url, c.last_scraped,
        a.name           AS agency_name,
        am.water         AS has_water,
        am.restrooms     AS has_restrooms,
        am.body_of_water AS body_of_water,
        am.amenities_raw AS amenities_raw,
        su.is_open       AS is_open,
        r.reservation_url AS booking_url,
        r.is_reservable   AS is_reservable,
        r.provider        AS booking_provider
    FROM campsites c
    LEFT JOIN agencies a        ON a.id = c.agency_id
    LEFT JOIN amenities am      ON am.campsite_id = c.id
    LEFT JOIN status_updates su ON su.campsite_id = c.id
    LEFT JOIN reservations r    ON r.campsite_id = c.id
    WHERE c.id = %s;
"""


# useful for weather data and anything that needs the campsite site_url: so things like updating site status and when it was last updated
# returns the full campsites row + agency/amenities/status/reservation + images
def get_campsite_by_id(campsite_id):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute(CAMPSITE_SQL, (campsite_id,))
    row = cur.fetchone()

    images = []
    if row:
        cur.execute(
            """
            SELECT image_url, description
            FROM images
            WHERE campsite_id = %s
            ORDER BY id
            LIMIT 8
            """,
            (campsite_id,),
        )
        images = [dict(r) for r in cur.fetchall()]

    cur.close()
    conn.close()

    if not row:
        return None

    site = dict(row)

    # Decimals -> float so jsonify and Jinja rounding both behave.
    for key in ("latitude", "longitude"):
        if site.get(key) is not None:
            site[key] = float(site[key])

    site["images"] = images
    site["hero_image"] = site.get("primary_image_url") or (
        images[0]["image_url"] if images else None
    )
    # Prefer a real reservation row's URL, fall back to the campsites column.
    site["booking_url"] = site.get("booking_url") or site.get("reservation_url")
    return site


# Nearby hikes from AllTrails (migration 0011). Ordered by trailhead distance so
# the closest trails render first; falls back to rating when distance is missing.
TRAILS_SQL = """
    SELECT
        t.id, t.name, t.url, t.difficulty, t.route_type,
        t.length_miles, t.elevation_gain_feet, t.avg_rating,
        t.reviews_count, t.photo_url, t.activities, t.features,
        ct.distance_miles
    FROM campsite_trails ct
    JOIN trails t ON t.id = ct.trail_id
    WHERE ct.campsite_id = %s
    ORDER BY ct.distance_miles ASC NULLS LAST,
             t.avg_rating DESC NULLS LAST
    LIMIT %s;
"""


def get_trails_for_campsite(campsite_id, limit=12):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(TRAILS_SQL, (campsite_id, limit))
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()

    for r in rows:
        for key in ("length_miles", "elevation_gain_feet", "avg_rating", "distance_miles"):
            if r.get(key) is not None:
                r[key] = float(r[key])
    return rows
