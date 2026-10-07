import psycopg2
from psycopg2.extras import RealDictCursor, Json

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
                LIMIT 30
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
               c.is_free, c.reservation_type,
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


# --- event logging (migration 0023) -----------------------------------------

ALLOWED_EVENT_TYPES = frozenset({
    'search_performed',
    'result_impression',
    'result_clicked',
    'campsite_viewed',
    'saved',
    'unsaved',
    'collection_added',
    'review_submitted',
    'search_feedback',
})


def log_event(event_type, user_id=None, anon_id=None, session_id=None,
              campsite_id=None, position=None, query_text=None, filters=None,
              result_ids=None, meta=None):
    """Log a user behavior event to the user_events table (migration 0023).

    Best-effort: any database failure is silently swallowed so event logging
    can never break a page load. The connection is always closed, even on
    failure, to prevent connection leaks.

    Args:
        event_type: Required. One of ALLOWED_EVENT_TYPES.
        user_id: Optional. The authenticated user's ID.
        anon_id: Optional. The anonymous visitor's UUID (from cookie).
        session_id: Optional. The browser session UUID.
        campsite_id: Optional. The campsite ID (for campsite-related events).
        position: Optional. 1-based rank in result list (for impression/click).
        query_text: Optional. The search query string.
        filters: Optional. Dict of parsed facet filters (converted to jsonb).
        result_ids: Optional. List of campsite IDs returned (converted to int[]).
        meta: Optional. Dict of additional metadata (converted to jsonb).
    """
    if event_type not in ALLOWED_EVENT_TYPES:
        return

    if meta is None:
        meta = {}

    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO user_events "
            "(user_id, anon_id, session_id, event_type, campsite_id, "
            "position, query_text, filters, result_ids, meta) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                user_id,
                anon_id,
                session_id,
                event_type,
                campsite_id,
                position,
                query_text,
                Json(filters) if filters else None,
                result_ids,
                Json(meta),
            ),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        if conn is not None:
            conn.close()


# --- users & saved campsites (migration 0014) ------------------------------

# is_admin belongs here. admin_required reads current_user()["is_admin"], and
# current_user() is built from this list, so leaving it out makes every admin
# route 404 for real admins while any test that mocks current_user still
# passes. That is exactly how it shipped broken the first time.
_USER_COLS = (
    "id, username, first_name, last_name, email, "
    "home_address, home_lat, home_lon, avatar_path, created_at, is_admin"
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
    cur.execute(
        """
        SELECT col.id, col.name, col.created_at, col.is_public,
               cc.campsite_id, c.name AS campsite_name, cc.added_at
        FROM collections col
        LEFT JOIN collection_campsites cc ON cc.collection_id = col.id
        LEFT JOIN campsites c ON c.id = cc.campsite_id
        WHERE col.user_id = %s
        ORDER BY col.name, cc.added_at;
        """,
        (user_id,),
    )
    coll_rows = cur.fetchall()
    cur.close()
    conn.close()

    out = _shape_user(profile) or {}
    for row in saved:
        row["saved_at"] = row["saved_at"].isoformat() if row.get("saved_at") else None
    out["saved_campsites"] = saved

    collections = {}
    for r in coll_rows:
        col = collections.setdefault(r["id"], {
            "id": r["id"], "name": r["name"], "is_public": r["is_public"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "campsites": [],
        })
        if r["campsite_id"] is not None:
            col["campsites"].append({
                "campsite_id": r["campsite_id"], "name": r["campsite_name"],
                "added_at": r["added_at"].isoformat() if r["added_at"] else None,
            })
    out["collections"] = list(collections.values())
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
        SELECT c.id, c.name, c.created_at, c.is_public,
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
        "SELECT id, name, created_at, is_public FROM collections "
        "WHERE id = %s AND user_id = %s;",
        (collection_id, user_id),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def set_collection_public(user_id, collection_id, is_public):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE collections SET is_public = %s WHERE id = %s AND user_id = %s;",
        (bool(is_public), collection_id, user_id),
    )
    conn.commit()
    cur.close()
    conn.close()


# --- public /users directory (migration 0021) --------------------------------

def get_public_users():
    """Users with at least one public collection: username, first name, and how
    many public collections they have. No email / address ever leaves here."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT u.username, u.first_name, u.avatar_path,
               count(*) AS public_collections
        FROM collections c
        JOIN users u ON u.id = c.user_id
        WHERE c.is_public
        GROUP BY u.id, u.username, u.first_name, u.avatar_path
        ORDER BY lower(u.username);
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def get_public_profile(username):
    """(user dict, [public collections]) for a username, or (None, None).

    The user dict is deliberately thin: username, first_name, avatar_path only.
    """
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT id, username, first_name, avatar_path FROM users WHERE lower(username) = lower(%s);",
        (username,),
    )
    user = cur.fetchone()
    if not user:
        cur.close()
        conn.close()
        return None, None
    cur.execute(
        """
        SELECT c.id, c.name, c.created_at, count(cc.campsite_id) AS count
        FROM collections c
        LEFT JOIN collection_campsites cc ON cc.collection_id = c.id
        WHERE c.user_id = %s AND c.is_public
        GROUP BY c.id
        ORDER BY c.name;
        """,
        (user["id"],),
    )
    collections = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return {k: user[k] for k in ("username", "first_name", "avatar_path")}, collections


def get_public_collection(username, collection_id):
    """(collection dict, [campsites]) for a public collection, or (None, None)."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT c.id, c.name, c.created_at, u.username AS owner
        FROM collections c
        JOIN users u ON u.id = c.user_id
        WHERE c.id = %s AND c.is_public AND lower(u.username) = lower(%s);
        """,
        (collection_id, username),
    )
    coll = cur.fetchone()
    cur.close()
    conn.close()
    if not coll:
        return None, None
    return dict(coll), get_collection_campsites(collection_id)


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


# --- anonymous identity (migration 0023) ------------------------------------

def merge_anon_events(user_id, anon_id):
    """Merge anonymous event history to a newly signed-in user (best-effort).

    When an anonymous visitor signs up or logs in, transfer all their
    behavioral history from anon_id to user_id. The AND user_id IS NULL guard
    ensures we never reassign rows that already belong to someone.

    This is best-effort: if it fails (network, concurrent conflict), the
    login/signup must still succeed. Call this after creating or verifying
    the user account.
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE user_events
            SET user_id = %s, anon_id = NULL
            WHERE anon_id = %s AND user_id IS NULL
            """,
            (user_id, anon_id),
        )
        conn.commit()
    except Exception:
        # Swallowed like record_pick, so a failed merge never fails a login.
        # Losing some anonymous history is survivable; losing the login is not.
        pass
    finally:
        # The close has to be in finally. Closing only on the happy path leaks
        # a connection on every failure, and enough leaks exhaust the server's
        # connection limit, which takes the whole app down. That is a far worse
        # outcome than the lost history this swallow is protecting against.
        if conn is not None:
            conn.close()


# --- reviews (migration 0024) -----------------------------------------------

def create_review(user_id, campsite_id, verdict, body, visited, attribute_reports):
    """Create a review and its attribute reports in one transaction.

    Args:
        user_id: The authenticated user's ID.
        campsite_id: The campsite being reviewed.
        verdict: Boolean (True = thumbs up, False = thumbs down).
        body: Optional text of the review.
        visited: Boolean whether the user claims to have visited.
        attribute_reports: List of dicts with keys: attribute, claimed_value.
                          Each attribute must be one of the allowed six.

    Raises:
        psycopg2.IntegrityError: On UNIQUE constraint violation (user already
                                 reviewed this campsite).
        psycopg2.DatabaseError: On attribute name validation failure or other
                                database errors.

    Returns:
        The review ID.
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Insert the review.
        cur.execute(
            """
            INSERT INTO reviews (user_id, campsite_id, verdict, body, visited, status)
            VALUES (%s, %s, %s, %s, %s, 'published')
            RETURNING id
            """,
            (user_id, campsite_id, verdict, body, visited),
        )
        review_id = cur.fetchone()[0]

        # Insert attribute reports if any.
        if attribute_reports:
            for report in attribute_reports:
                cur.execute(
                    """
                    INSERT INTO review_attribute_reports
                        (review_id, campsite_id, attribute, claimed_value)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (review_id, campsite_id, report["attribute"], report.get("claimed_value")),
                )

        conn.commit()
        return review_id

    except Exception:
        if conn:
            conn.rollback()
        raise

    finally:
        if conn:
            conn.close()


def get_review_by_user_and_campsite(user_id, campsite_id):
    """Fetch a user's review of a campsite, if one exists.

    Returns:
        A dict with keys (id, verdict, body, visited, status, created_at, updated_at),
        or None if no review exists.
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            SELECT id, verdict, body, visited, status, created_at, updated_at
            FROM reviews
            WHERE user_id = %s AND campsite_id = %s
            """,
            (user_id, campsite_id),
        )
        return cur.fetchone()

    finally:
        if conn:
            conn.close()


def count_reviews_today(user_id):
    """Count how many reviews the user has submitted today.

    Returns:
        The count as an integer.
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*)
            FROM reviews
            WHERE user_id = %s
            AND created_at >= now()::date
            """,
            (user_id,),
        )
        return cur.fetchone()[0]

    finally:
        if conn:
            conn.close()


def create_review_photo(review_id, path):
    """Create a pending review photo.

    Args:
        review_id: The review this photo belongs to.
        path: The file path to the photo.

    Returns:
        The photo ID.

    Raises:
        psycopg2.DatabaseError: On database errors.
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO review_photos (review_id, path, status)
            VALUES (%s, %s, 'pending')
            RETURNING id
            """,
            (review_id, path),
        )
        photo_id = cur.fetchone()[0]
        conn.commit()
        return photo_id

    except Exception:
        if conn:
            conn.rollback()
        raise

    finally:
        if conn:
            conn.close()


def get_review_photos(review_id):
    """Fetch all photos for a review, sorted by creation date.

    Args:
        review_id: The review ID.

    Returns:
        List of dicts with keys (id, path, status, created_at).
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            SELECT id, path, status, created_at
            FROM review_photos
            WHERE review_id = %s
            ORDER BY created_at ASC
            """,
            (review_id,),
        )
        return cur.fetchall() or []

    finally:
        if conn:
            conn.close()


def count_review_photos(review_id):
    """Count how many photos are attached to a review (any status).

    Args:
        review_id: The review ID.

    Returns:
        The count as an integer.
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*)
            FROM review_photos
            WHERE review_id = %s
            """,
            (review_id,),
        )
        return cur.fetchone()[0]

    finally:
        if conn:
            conn.close()


def get_reviews_for_campsite(campsite_id, limit=20, offset=0):
    """Fetch published reviews for a campsite, newest first, with author info and approved photos.

    Args:
        campsite_id: The campsite ID.
        limit: Number of reviews to return (default 20).
        offset: Pagination offset (default 0).

    Returns:
        List of dicts with keys: id, verdict, body, created_at, author_username,
        author_first_name, author_avatar_path, approved_photos (list of photo dicts).
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Fetch reviews with author info
        cur.execute(
            """
            SELECT
                r.id, r.verdict, r.body, r.created_at,
                u.username AS author_username,
                u.first_name AS author_first_name,
                u.avatar_path AS author_avatar_path
            FROM reviews r
            JOIN users u ON u.id = r.user_id
            WHERE r.campsite_id = %s AND r.status = 'published'
            ORDER BY r.created_at DESC
            LIMIT %s
            OFFSET %s
            """,
            (campsite_id, limit, offset),
        )
        reviews = [dict(r) for r in cur.fetchall()]

        # Fetch approved photos for each review
        for review in reviews:
            cur.execute(
                """
                SELECT id, path, created_at
                FROM review_photos
                WHERE review_id = %s AND status = 'approved'
                ORDER BY created_at ASC
                """,
                (review["id"],),
            )
            review["approved_photos"] = [dict(r) for r in cur.fetchall()]

        return reviews

    finally:
        if conn:
            conn.close()


def count_reviews_for_campsite(campsite_id):
    """Count published reviews for a campsite.

    Args:
        campsite_id: The campsite ID.

    Returns:
        The count as an integer.
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*)
            FROM reviews
            WHERE campsite_id = %s AND status = 'published'
            """,
            (campsite_id,),
        )
        return cur.fetchone()[0]

    finally:
        if conn:
            conn.close()


def get_review_verdict_counts(campsite_id):
    """Get the count of thumbs up and thumbs down for published reviews.

    Args:
        campsite_id: The campsite ID.

    Returns:
        A dict with keys 'up' and 'down' containing counts.
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN verdict = TRUE THEN 1 ELSE 0 END), 0) AS up,
                COALESCE(SUM(CASE WHEN verdict = FALSE THEN 1 ELSE 0 END), 0) AS down
            FROM reviews
            WHERE campsite_id = %s AND status = 'published'
            """,
            (campsite_id,),
        )
        row = cur.fetchone()
        return {"up": row[0], "down": row[1]}

    finally:
        if conn:
            conn.close()


# --- admin/moderation (migration 0025) ----------------------------------------


def get_pending_photos():
    """Get all review photos awaiting approval.

    Returns:
        A list of dicts with keys: id, review_id, path, created_at, review_user,
        campsite_id, campsite_name.
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            SELECT
                rp.id,
                rp.review_id,
                rp.path,
                rp.created_at,
                u.username AS review_user,
                r.campsite_id,
                c.name AS campsite_name
            FROM review_photos rp
            JOIN reviews r ON r.id = rp.review_id
            JOIN users u ON u.id = r.user_id
            JOIN campsites c ON c.id = r.campsite_id
            WHERE rp.status = 'pending'
            ORDER BY rp.created_at ASC
            """,
        )
        return [dict(row) for row in cur.fetchall()]

    finally:
        if conn:
            conn.close()


def get_open_reports():
    """Get all open content reports.

    Returns:
        A list of dicts with keys: id, reporter_user, target_type, target_id,
        reason, created_at, review_body (if review), campsite_name, campsite_id.
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            SELECT
                cr.id,
                COALESCE(u.username, 'Anonymous') AS reporter_user,
                cr.target_type,
                cr.target_id,
                cr.reason,
                cr.created_at,
                r.body AS review_body,
                r.campsite_id,
                c.name AS campsite_name
            FROM content_reports cr
            LEFT JOIN users u ON u.id = cr.reporter_id
            LEFT JOIN reviews r ON cr.target_type = 'review' AND cr.target_id = r.id
            LEFT JOIN campsites c ON c.id = r.campsite_id
            WHERE cr.status = 'open'
            ORDER BY cr.created_at ASC
            """,
        )
        return [dict(row) for row in cur.fetchall()]

    finally:
        if conn:
            conn.close()


def get_attribute_report_groups():
    """Get attribute reports grouped by campsite and attribute with counts.

    Returns:
        A list of dicts with keys: campsite_id, campsite_name, attribute,
        claimed_value, report_count.
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            SELECT
                c.id AS campsite_id,
                c.name AS campsite_name,
                rar.attribute,
                rar.claimed_value,
                COUNT(*) AS report_count
            FROM review_attribute_reports rar
            JOIN campsites c ON c.id = rar.campsite_id
            GROUP BY c.id, c.name, rar.attribute, rar.claimed_value
            ORDER BY report_count DESC, c.name ASC, rar.attribute ASC
            """,
        )
        return [dict(row) for row in cur.fetchall()]

    finally:
        if conn:
            conn.close()


def set_photo_status(photo_id, status, admin_id):
    """Set a review photo's status and record the moderation action.

    Args:
        photo_id: The review photo ID.
        status: One of 'approved' or 'rejected'.
        admin_id: The admin user ID performing the action.

    Raises:
        ValueError: If status is invalid.
        psycopg2.DatabaseError: On database errors.
    """
    if status not in ('approved', 'rejected'):
        raise ValueError("Status must be 'approved' or 'rejected'.")

    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Update photo status.
        cur.execute(
            "UPDATE review_photos SET status = %s WHERE id = %s",
            (status, photo_id),
        )

        # Record the action (action is either 'approve' or 'reject').
        action = 'approve' if status == 'approved' else 'reject'
        cur.execute(
            """
            INSERT INTO moderation_actions (admin_id, action, target_type, target_id)
            VALUES (%s, %s, 'review_photo', %s)
            """,
            (admin_id, action, photo_id),
        )

        conn.commit()

    except Exception:
        if conn:
            conn.rollback()
        raise

    finally:
        if conn:
            conn.close()


def set_review_status(review_id, status, admin_id):
    """Set a review's status and record the moderation action.

    Args:
        review_id: The review ID.
        status: One of 'published' or 'removed'.
        admin_id: The admin user ID performing the action.

    Raises:
        ValueError: If status is invalid.
        psycopg2.DatabaseError: On database errors.
    """
    if status not in ('published', 'removed'):
        raise ValueError("Status must be 'published' or 'removed'.")

    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Update review status.
        cur.execute(
            "UPDATE reviews SET status = %s WHERE id = %s",
            (status, review_id),
        )

        # Record the action (action is either 'restore' or 'remove').
        action = 'restore' if status == 'published' else 'remove'
        cur.execute(
            """
            INSERT INTO moderation_actions (admin_id, action, target_type, target_id)
            VALUES (%s, %s, 'review', %s)
            """,
            (admin_id, action, review_id),
        )

        conn.commit()

    except Exception:
        if conn:
            conn.rollback()
        raise

    finally:
        if conn:
            conn.close()


def set_report_status(report_id, status, admin_id):
    """Set a content report's status and record the moderation action.

    Args:
        report_id: The content report ID.
        status: One of 'actioned' or 'dismissed'.
        admin_id: The admin user ID performing the action.

    Raises:
        ValueError: If status is invalid.
        psycopg2.DatabaseError: On database errors.
    """
    if status not in ('actioned', 'dismissed'):
        raise ValueError("Status must be 'actioned' or 'dismissed'.")

    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Update report status.
        cur.execute(
            "UPDATE content_reports SET status = %s WHERE id = %s",
            (status, report_id),
        )

        # Record the action (action is either 'action' or 'dismiss').
        action = 'dismiss' if status == 'dismissed' else 'action'
        cur.execute(
            """
            INSERT INTO moderation_actions (admin_id, action, target_type, target_id)
            VALUES (%s, %s, 'review', %s)
            """,
            (admin_id, action, report_id),
        )

        conn.commit()

    except Exception:
        if conn:
            conn.rollback()
        raise

    finally:
        if conn:
            conn.close()


# Attribute correction thresholds: auto-apply only when at least N distinct
# users agree (and agreement >= X%). Both gates must pass independently.
ATTRIBUTE_THRESHOLD_MIN_USERS = 3
ATTRIBUTE_THRESHOLD_MIN_PERCENT = 75


def evaluate_attribute_reports():
    """Evaluate attribute reports to determine which meet approval thresholds.

    Counts distinct users per (campsite, attribute, claimed_value) group and
    calculates whether each group meets the approval thresholds (distinct user
    count >= ATTRIBUTE_THRESHOLD_MIN_USERS AND agreement percentage >=
    ATTRIBUTE_THRESHOLD_MIN_PERCENT).

    Returns:
        A list of dicts with keys: campsite_id, campsite_name, attribute,
        claimed_value, agreeing_users (distinct count), total_users (distinct
        users who reported this campsite + attribute), agreement_percent,
        meets_threshold (boolean).
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # For each (campsite, attribute, claimed_value) group, count distinct
        # users who reported that exact value, and count all distinct users who
        # reported anything for that (campsite, attribute) pair.
        cur.execute(
            """
            WITH group_stats AS (
                SELECT
                    campsite_id,
                    attribute,
                    claimed_value,
                    COUNT(DISTINCT review_id) AS report_count,
                    COUNT(DISTINCT COALESCE((
                        SELECT user_id FROM reviews r WHERE r.id = rar.review_id
                    ), -1)) AS agreeing_users
                FROM review_attribute_reports rar
                GROUP BY campsite_id, attribute, claimed_value
            ),
            campsite_attribute_stats AS (
                SELECT
                    rar.campsite_id,
                    rar.attribute,
                    COUNT(DISTINCT COALESCE((
                        SELECT user_id FROM reviews r WHERE r.id = rar.review_id
                    ), -1)) AS total_users_for_pair
                FROM review_attribute_reports rar
                GROUP BY rar.campsite_id, rar.attribute
            )
            SELECT
                gs.campsite_id,
                c.name AS campsite_name,
                gs.attribute,
                gs.claimed_value,
                gs.agreeing_users,
                cas.total_users_for_pair AS total_users,
                ROUND(100.0 * gs.agreeing_users / NULLIF(cas.total_users_for_pair, 0)) AS agreement_percent,
                (gs.agreeing_users >= %s AND
                 100.0 * gs.agreeing_users / NULLIF(cas.total_users_for_pair, 0) >= %s) AS meets_threshold
            FROM group_stats gs
            JOIN campsite_attribute_stats cas ON
                cas.campsite_id = gs.campsite_id AND
                cas.attribute = gs.attribute
            JOIN campsites c ON c.id = gs.campsite_id
            ORDER BY gs.campsite_id, gs.attribute, gs.claimed_value
            """,
            (ATTRIBUTE_THRESHOLD_MIN_USERS, ATTRIBUTE_THRESHOLD_MIN_PERCENT),
        )
        return [dict(row) for row in cur.fetchall()]

    finally:
        if conn:
            conn.close()


def apply_attribute_correction(campsite_id, attribute, claimed_value, admin_id):
    """Apply a user-reported attribute correction to a campsite.

    Writes the claimed value to the correct column on the correct table, and
    records the action in an audit row, both within one atomic transaction.

    Attributes and their target columns:
      - water -> amenities.water_feature (TEXT)
      - toilet_type -> amenities.toilet_type (TEXT)
      - is_free -> campsites.is_free (BOOLEAN)
      - fee_min -> campsites.fee_min (NUMERIC)
      - is_reservable -> reservations.is_reservable (BOOLEAN)
      - is_open -> status_updates.is_open (BOOLEAN)

    Args:
        campsite_id: The campsite ID.
        attribute: One of the six allowed attributes.
        claimed_value: The value to apply (will be coerced to the column type).
        admin_id: The admin user ID performing the action.

    Raises:
        ValueError: If attribute is not allowed or claimed_value has the wrong type.
        psycopg2.DatabaseError: On database errors.
    """
    allowed_attributes = ('water', 'toilet_type', 'is_free', 'fee_min',
                         'is_reservable', 'is_open')
    if attribute not in allowed_attributes:
        raise ValueError(f"Attribute '{attribute}' is not allowed.")

    # Type coercion and validation by attribute.
    if attribute == 'water':
        # amenities.water is a BOOLEAN meaning "has drinking water". It is NOT
        # amenities.water_feature, which is the lake/creek/river TEXT facet.
        # Writing a water report into water_feature both fails to fix the
        # boolean and pollutes the facet the search filter reads.
        if isinstance(claimed_value, bool):
            coerced_value = claimed_value
        elif isinstance(claimed_value, str) and claimed_value.lower() in (
                'true', '1', 'yes'):
            coerced_value = True
        elif isinstance(claimed_value, str) and claimed_value.lower() in (
                'false', '0', 'no'):
            coerced_value = False
        else:
            raise ValueError(
                "water must be a boolean, got '%s'." % (claimed_value,))
    elif attribute == 'toilet_type':
        # TEXT column, constrained to known values in the app
        allowed_types = ('flush', 'vault', 'none')
        if claimed_value not in allowed_types:
            raise ValueError(f"toilet_type must be one of {allowed_types}.")
        coerced_value = claimed_value
    elif attribute == 'is_free':
        # BOOLEAN column
        if isinstance(claimed_value, bool):
            coerced_value = claimed_value
        elif isinstance(claimed_value, str):
            if claimed_value.lower() in ('true', '1', 'yes'):
                coerced_value = True
            elif claimed_value.lower() in ('false', '0', 'no'):
                coerced_value = False
            else:
                raise ValueError(f"is_free must be a boolean, got '{claimed_value}'.")
        else:
            raise ValueError(f"is_free must be a boolean, got {type(claimed_value).__name__}.")
    elif attribute == 'fee_min':
        # NUMERIC(8, 2) column
        try:
            coerced_value = float(claimed_value)
        except (ValueError, TypeError):
            raise ValueError(f"fee_min must be numeric, got '{claimed_value}'.")
    elif attribute == 'is_reservable':
        # BOOLEAN column
        if isinstance(claimed_value, bool):
            coerced_value = claimed_value
        elif isinstance(claimed_value, str):
            if claimed_value.lower() in ('true', '1', 'yes'):
                coerced_value = True
            elif claimed_value.lower() in ('false', '0', 'no'):
                coerced_value = False
            else:
                raise ValueError(f"is_reservable must be a boolean, got '{claimed_value}'.")
        else:
            raise ValueError(f"is_reservable must be a boolean, got {type(claimed_value).__name__}.")
    elif attribute == 'is_open':
        # BOOLEAN column
        if isinstance(claimed_value, bool):
            coerced_value = claimed_value
        elif isinstance(claimed_value, str):
            if claimed_value.lower() in ('true', '1', 'yes'):
                coerced_value = True
            elif claimed_value.lower() in ('false', '0', 'no'):
                coerced_value = False
            else:
                raise ValueError(f"is_open must be a boolean, got '{claimed_value}'.")
        else:
            raise ValueError(f"is_open must be a boolean, got {type(claimed_value).__name__}.")

    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Update the appropriate column based on attribute.
        if attribute == 'water':
            cur.execute(
                "UPDATE amenities SET water = %s WHERE campsite_id = %s",
                (coerced_value, campsite_id),
            )
        elif attribute == 'toilet_type':
            cur.execute(
                "UPDATE amenities SET toilet_type = %s WHERE campsite_id = %s",
                (coerced_value, campsite_id),
            )
        elif attribute == 'is_free':
            cur.execute(
                "UPDATE campsites SET is_free = %s WHERE id = %s",
                (coerced_value, campsite_id),
            )
        elif attribute == 'fee_min':
            cur.execute(
                "UPDATE campsites SET fee_min = %s WHERE id = %s",
                (coerced_value, campsite_id),
            )
        elif attribute == 'is_reservable':
            cur.execute(
                "UPDATE reservations SET is_reservable = %s WHERE campsite_id = %s",
                (coerced_value, campsite_id),
            )
        elif attribute == 'is_open':
            cur.execute(
                "UPDATE status_updates SET is_open = %s WHERE campsite_id = %s",
                (coerced_value, campsite_id),
            )

        # Record the approval action.
        cur.execute(
            """
            INSERT INTO moderation_actions (admin_id, action, target_type, target_id)
            VALUES (%s, 'approve', 'attribute_report', %s)
            """,
            (admin_id, campsite_id),
        )

        conn.commit()

    except Exception:
        if conn:
            conn.rollback()
        raise

    finally:
        if conn:
            conn.close()
