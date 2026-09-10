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
        c.approx_latitude, c.approx_longitude, c.approx_coord_source,
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
    # Callers pass this straight from request args; a non-numeric value would
    # otherwise raise inside execute() and leak the connection.
    try:
        campsite_id = int(campsite_id)
    except (TypeError, ValueError):
        return None

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
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
    finally:
        cur.close()
        conn.close()

    if not row:
        return None

    site = dict(row)

    # Decimals -> float so jsonify and Jinja rounding both behave.
    for key in ("latitude", "longitude", "approx_latitude", "approx_longitude",
                "fee_min", "fee_max"):
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


def get_campsites_with_thumbs():
    """Every campsite that has coordinates, plus its first image URL.

    Powers the homepage "Campsites near you" carousel. Distance ranking is done
    in the route so this stays a plain, cacheable scan.
    """
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT c.id, c.name, c.forest_name, c.latitude, c.longitude,
               COALESCE(c.primary_image_url, img.image_url) AS image_url
        FROM campsites c
        LEFT JOIN LATERAL (
            SELECT image_url FROM images
            WHERE campsite_id = c.id
            ORDER BY id
            LIMIT 1
        ) img ON TRUE
        WHERE c.latitude IS NOT NULL AND c.longitude IS NOT NULL
          AND c.latitude <> 'NaN'::numeric AND c.longitude <> 'NaN'::numeric;
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    for r in rows:
        for key in ("latitude", "longitude"):
            r[key] = float(r[key]) if r[key] is not None else None
    return rows


def get_suggested_campsites(user_id, limit=12):
    """A rough "suggested for you" deck from what the user has already saved or
    put in a collection.

    Not the ML ranker from the roadmap — a transparent overlap score: build a
    taste profile (forests, terrain bands, activities, water features, free vs
    paid) from the user's saved + collection campsites, then rank every other
    campsite by how much it shares, breaking ties on global pick_count. Returns
    [] when there isn't enough signal (fewer than 2 liked sites).
    """
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # everything the user has signalled a liking for
    cur.execute(
        """
        SELECT DISTINCT c.id, c.forest_name, c.terrain, c.is_free,
               c.reservation_type, a.water_feature, a.activities
        FROM campsites c
        LEFT JOIN amenities a ON a.campsite_id = c.id
        WHERE c.id IN (
            SELECT campsite_id FROM saved_campsites WHERE user_id = %(uid)s
            UNION
            SELECT cc.campsite_id FROM collection_campsites cc
            JOIN collections col ON col.id = cc.collection_id
            WHERE col.user_id = %(uid)s
        )
        """,
        {"uid": user_id},
    )
    liked = cur.fetchall()
    if len(liked) < 2:
        cur.close()
        conn.close()
        return []

    liked_ids = {r["id"] for r in liked}

    def _tally(key):
        counts = {}
        for r in liked:
            v = r[key]
            if v:
                counts[v] = counts.get(v, 0) + 1
        return counts

    forests = _tally("forest_name")
    terrains = _tally("terrain")
    waters = _tally("water_feature")
    rtypes = _tally("reservation_type")
    acts = {}
    for r in liked:
        for a in (r["activities"] or []):
            acts[a] = acts.get(a, 0) + 1
    free_share = sum(1 for r in liked if r["is_free"]) / len(liked)
    n = len(liked)

    # candidate pool: everything with coordinates + its attributes + a thumb
    cur.execute(
        """
        SELECT c.id, c.name, c.forest_name, c.terrain, c.is_free,
               c.reservation_type, c.pick_count,
               a.water_feature, a.activities,
               COALESCE(c.primary_image_url, img.image_url) AS image_url
        FROM campsites c
        LEFT JOIN amenities a ON a.campsite_id = c.id
        LEFT JOIN LATERAL (
            SELECT image_url FROM images WHERE campsite_id = c.id ORDER BY id LIMIT 1
        ) img ON TRUE
        WHERE c.latitude IS NOT NULL AND c.longitude IS NOT NULL
        """
    )
    pool = cur.fetchall()
    cur.close()
    conn.close()

    scored = []
    for r in pool:
        if r["id"] in liked_ids:
            continue
        s = 0.0
        s += 3.0 * forests.get(r["forest_name"], 0) / n
        s += 2.0 * terrains.get(r["terrain"], 0) / n
        s += 2.0 * waters.get(r["water_feature"], 0) / n
        s += 1.0 * rtypes.get(r["reservation_type"], 0) / n
        overlap = sum(acts.get(a, 0) for a in (r["activities"] or []))
        s += 1.5 * overlap / n
        if r["is_free"] and free_share >= 0.5:
            s += 0.75
        if s <= 0:
            continue
        if r["image_url"]:
            s += 0.4  # a card with a photo is a better suggestion
        scored.append((s, r["pick_count"] or 0, r))

    scored.sort(key=lambda x: (-x[0], -x[1], (x[2]["name"] or "").lower()))
    out = []
    for _, _, r in scored[:limit]:
        out.append({
            "id": r["id"], "name": r["name"],
            "forest_name": r["forest_name"], "image_url": r["image_url"],
        })
    return out


def record_pick(campsite_id):
    """Bump a campsite's selection counter (migration 0017).

    Called when someone opens a campsite from a results list. Best-effort: a
    bad id just updates nothing, and any DB hiccup is swallowed so a tracking
    ping can never break navigation.
    """
    try:
        campsite_id = int(campsite_id)
    except (TypeError, ValueError):
        return
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE campsites "
            "SET pick_count = pick_count + 1, last_picked_at = now() "
            "WHERE id = %s",
            (campsite_id,),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        if conn is not None:
            conn.close()


# --- users & saved campsites (migration 0014) ------------------------------

_USER_COLS = (
    "id, username, first_name, last_name, email, "
    "home_address, home_lat, home_lon, avatar_path, created_at"
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
        "home_address", "home_lat", "home_lon", "avatar_path",
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


# --- collections (migration 0016) ----------------------------------------

def create_collection(user_id, name):
    """Returns the new collection dict, or None if the name is already used."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            "INSERT INTO collections (user_id, name) VALUES (%s, %s) "
            "RETURNING id, name, created_at;",
            (user_id, name),
        )
        row = cur.fetchone()
        conn.commit()
        return dict(row)
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return None
    finally:
        cur.close()
        conn.close()


def get_collections(user_id):
    """A user's collections with a member count each."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT c.id, c.name, c.created_at,
               count(cc.campsite_id) AS count
        FROM collections c
        LEFT JOIN collection_campsites cc ON cc.collection_id = c.id
        WHERE c.user_id = %s
        GROUP BY c.id
        ORDER BY c.name;
        """,
        (user_id,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def get_collection(user_id, collection_id):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT id, name, created_at FROM collections WHERE id = %s AND user_id = %s;",
        (collection_id, user_id),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def delete_collection(user_id, collection_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM collections WHERE id = %s AND user_id = %s;",
        (collection_id, user_id),
    )
    conn.commit()
    cur.close()
    conn.close()


def add_to_collection(user_id, collection_id, campsite_id):
    """No-op if the collection isn't the user's, or the campsite is already in it."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO collection_campsites (collection_id, campsite_id)
        SELECT %s, %s
        WHERE EXISTS (SELECT 1 FROM collections WHERE id = %s AND user_id = %s)
        ON CONFLICT DO NOTHING;
        """,
        (collection_id, campsite_id, collection_id, user_id),
    )
    conn.commit()
    cur.close()
    conn.close()


def remove_from_collection(user_id, collection_id, campsite_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM collection_campsites cc
        USING collections c
        WHERE cc.collection_id = c.id
          AND c.user_id = %s AND c.id = %s AND cc.campsite_id = %s;
        """,
        (user_id, collection_id, campsite_id),
    )
    conn.commit()
    cur.close()
    conn.close()


def get_collection_campsites(collection_id):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT c.id, c.name, c.forest_name, c.terrain, c.elevation_ft,
               c.fee, c.is_free, c.reservation_type, cc.added_at
        FROM collection_campsites cc
        JOIN campsites c ON c.id = cc.campsite_id
        WHERE cc.collection_id = %s
        ORDER BY cc.added_at DESC;
        """,
        (collection_id,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows
