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
        c.seasons_of_use, c.num_sites, c.overview,
        c.fee, c.fee_min, c.fee_max, c.is_free,
        c.elevation_ft, c.terrain,
        c.site_url, c.source, c.primary_image_url, c.last_scraped,
        a.name           AS agency_name,
        a.level          AS agency_level,
        am.water         AS has_water,
        am.restrooms     AS has_restrooms,
        am.body_of_water AS body_of_water,
        am.water_feature AS water_feature,
        am.toilet_type   AS toilet_type,
        am.activities    AS activities,
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
    for key in ("latitude", "longitude", "fee_min", "fee_max"):
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


# --- users & saved campsites (migration 0014) ------------------------------

_USER_COLS = (
    "id, username, first_name, last_name, email, "
    "home_address, home_lat, home_lon, created_at"
)


def _shape_user(row):
    if not row:
        return None
    user = dict(row)
    for key in ("home_lat", "home_lon"):
        if user.get(key) is not None:
            user[key] = float(user[key])
    return user


def create_user(username, password_hash, **profile):
    """Insert a user. Returns the new user dict, or None on a username clash."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            f"""
            INSERT INTO users
                (username, password_hash, first_name, last_name, email,
                 home_address, home_lat, home_lon)
            VALUES (%(username)s, %(password_hash)s, %(first_name)s, %(last_name)s,
                    %(email)s, %(home_address)s, %(home_lat)s, %(home_lon)s)
            RETURNING {_USER_COLS};
            """,
            {
                "username": username,
                "password_hash": password_hash,
                "first_name": profile.get("first_name"),
                "last_name": profile.get("last_name"),
                "email": profile.get("email"),
                "home_address": profile.get("home_address"),
                "home_lat": profile.get("home_lat"),
                "home_lon": profile.get("home_lon"),
            },
        )
        row = cur.fetchone()
        conn.commit()
        return _shape_user(row)
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return None
    finally:
        cur.close()
        conn.close()


def get_user_for_login(username):
    """Full row including password_hash — only for verifying a login."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM users WHERE lower(username) = lower(%s);", (username,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def get_user(user_id):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(f"SELECT {_USER_COLS} FROM users WHERE id = %s;", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return _shape_user(row)


def update_user_profile(user_id, **fields):
    """Update the given profile columns (only keys that are passed)."""
    allowed = (
        "first_name", "last_name", "email",
        "home_address", "home_lat", "home_lon",
    )
    sets = {k: fields[k] for k in allowed if k in fields}
    if not sets:
        return get_user(user_id)
    assignments = ", ".join(f"{k} = %({k})s" for k in sets)
    sets["_id"] = user_id
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        f"UPDATE users SET {assignments} WHERE id = %(_id)s RETURNING {_USER_COLS};",
        sets,
    )
    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return _shape_user(row)


def export_user_data(user_id):
    """Everything stored for a user, for the settings-page export."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(f"SELECT {_USER_COLS} FROM users WHERE id = %s;", (user_id,))
    profile = cur.fetchone()
    cur.execute(
        """
        SELECT s.campsite_id, c.name, s.saved_at
        FROM saved_campsites s
        JOIN campsites c ON c.id = s.campsite_id
        WHERE s.user_id = %s
        ORDER BY s.saved_at DESC;
        """,
        (user_id,),
    )
    saved = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    out = _shape_user(profile) or {}
    for row in saved:
        row["saved_at"] = row["saved_at"].isoformat() if row.get("saved_at") else None
    out["saved_campsites"] = saved
    return out


def save_campsite(user_id, campsite_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO saved_campsites (user_id, campsite_id)
        VALUES (%s, %s)
        ON CONFLICT (user_id, campsite_id) DO NOTHING;
        """,
        (user_id, campsite_id),
    )
    conn.commit()
    cur.close()
    conn.close()


def unsave_campsite(user_id, campsite_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM saved_campsites WHERE user_id = %s AND campsite_id = %s;",
        (user_id, campsite_id),
    )
    conn.commit()
    cur.close()
    conn.close()


def is_campsite_saved(user_id, campsite_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM saved_campsites WHERE user_id = %s AND campsite_id = %s;",
        (user_id, campsite_id),
    )
    hit = cur.fetchone() is not None
    cur.close()
    conn.close()
    return hit


def get_saved_campsites(user_id):
    """Saved campsites for the account page: enough to render a result card."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT
            c.id, c.name, c.forest_name, c.latitude, c.longitude,
            c.terrain, c.elevation_ft, c.fee, c.is_free, c.reservation_type,
            s.saved_at
        FROM saved_campsites s
        JOIN campsites c ON c.id = s.campsite_id
        WHERE s.user_id = %s
        ORDER BY s.saved_at DESC;
        """,
        (user_id,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    for r in rows:
        for key in ("latitude", "longitude", "fee_min"):
            if r.get(key) is not None:
                val = float(r[key])
                r[key] = None if val != val else val
    return rows
