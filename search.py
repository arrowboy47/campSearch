from rapidfuzz import fuzz
from db import get_connection
import math
import re

# Popularity bonus: how many result-list click-throughs a campsite has had
# (campsites.pick_count, migration 0017) nudges its score. Log-scaled so the
# first few picks matter and a runaway favourite can't dominate, and hard-capped
# well below the gap between a real name match and a fuzzy one.
_POPULARITY_WEIGHT = 2.2
_POPULARITY_CAP = 8.0


def _popularity_bonus(pick_count):
    if not pick_count or pick_count < 1:
        return 0.0
    return min(_POPULARITY_CAP, _POPULARITY_WEIGHT * math.log1p(pick_count))


def normalize(text):
    """Lowercase and strip whitespace for fuzzy matching.

    NOTE: we intentionally remove *all* whitespace here so that queries like
    "pine crest" will still match "Pinecrest" and similar variations.
    """

    if text is None:
        return ""
    return re.sub(r"\s+", "", text.strip().lower())


# --- faceted filtering -------------------------------------------------------
#
# Every campsite row is joined to its one-to-one satellites (status, amenities,
# reservations, latest weather) once, here. Filters are applied in SQL so the
# fuzzy pass only ever scores an already-narrowed set. A filter that is None /
# empty is simply not added to the WHERE clause.

_BASE_SQL = """
    SELECT
        c.id,
        c.name,
        c.forest_name,
        c.latitude,
        c.longitude,
        c.source,
        c.reservation_type,
        c.num_sites,
        c.fee,
        c.fee_min,
        c.is_free,
        c.terrain,
        c.elevation_ft,
        c.pick_count,
        su.is_open,
        wf.forecast_json,
        am.water        AS has_water,
        am.restrooms    AS has_restrooms,
        am.toilet_type,
        am.water_feature,
        am.activities,
        r.is_reservable
    FROM campsites c
    LEFT JOIN status_updates    su ON c.id = su.campsite_id
    LEFT JOIN amenities         am ON c.id = am.campsite_id
    LEFT JOIN reservations      r  ON c.id = r.campsite_id
    -- weather_forecasts has one row PER DAY (migration 0004), so a plain join
    -- multiplies every campsite by its forecast-day count (~4250 rows for 2440
    -- campsites) and the row cap then silently drops real matches. Pull just
    -- the earliest forecast so the join stays 1:1.
    LEFT JOIN LATERAL (
        SELECT forecast_json
        FROM weather_forecasts
        WHERE campsite_id = c.id
        ORDER BY forecast_date NULLS LAST
        LIMIT 1
    ) wf ON TRUE
    WHERE 1 = 1
"""

_ROW_FIELDS = [
    "id", "name", "forest_name", "latitude", "longitude", "source",
    "reservation_type", "num_sites", "fee", "fee_min", "is_free", "terrain",
    "elevation_ft", "pick_count", "is_open", "forecast_json", "has_water", "has_restrooms",
    "toilet_type", "water_feature", "activities", "is_reservable",
]


def _row_to_dict(row):
    d = dict(zip(_ROW_FIELDS, row))
    for key in ("latitude", "longitude", "fee_min"):
        if d.get(key) is not None:
            val = float(d[key])
            d[key] = None if val != val else val  # drop NaN (bad coords)
    d["has_water"] = bool(d["has_water"]) if d["has_water"] is not None else False
    d["has_restrooms"] = bool(d["has_restrooms"]) if d["has_restrooms"] is not None else False
    d["activities"] = d.get("activities") or []
    # keep the key the results template already reads
    d["forecast"] = d.get("forecast_json")
    return d


def _build_where(filters):
    """(sql_fragment, params) for everything in `filters` that is set."""
    clauses = []
    params = []
    f = filters

    if f.get("is_open"):
        clauses.append("su.is_open = TRUE")

    if f.get("forest"):
        clauses.append("LOWER(c.forest_name) LIKE %s")
        params.append(f"%{f['forest'].strip().lower()}%")

    if f.get("water"):
        clauses.append("am.water = TRUE")

    toilet = f.get("toilet")
    if toilet in ("flush", "vault"):
        clauses.append("am.toilet_type = %s")
        params.append(toilet)
    elif toilet == "any":
        clauses.append("(am.toilet_type IS NOT NULL AND am.toilet_type <> 'none')")

    if f.get("free_only"):
        clauses.append("c.is_free = TRUE")
    elif f.get("fee_max") is not None:
        clauses.append("(c.is_free = TRUE OR c.fee_min <= %s)")
        params.append(f["fee_max"])

    if f.get("reservable"):
        clauses.append("r.is_reservable = TRUE")

    camping = f.get("camping_type")
    if camping == "dispersed":
        clauses.append("c.reservation_type = 'dispersed'")
    elif camping == "developed":
        clauses.append("(c.reservation_type IS DISTINCT FROM 'dispersed')")

    if f.get("elev_min") is not None:
        clauses.append("c.elevation_ft >= %s")
        params.append(f["elev_min"])
    if f.get("elev_max") is not None:
        clauses.append("c.elevation_ft <= %s")
        params.append(f["elev_max"])

    if f.get("terrain"):
        clauses.append("c.terrain = ANY(%s)")
        params.append(list(f["terrain"]))

    if f.get("water_feature"):
        clauses.append("am.water_feature = ANY(%s)")
        params.append(list(f["water_feature"]))

    if f.get("activities"):
        # match a site that offers ANY of the requested activities
        clauses.append("am.activities && %s")
        params.append(list(f["activities"]))

    frag = ("".join(f" AND {c}" for c in clauses))
    return frag, params


def _fetch_filtered(filters, hard_limit=5000):
    conn = get_connection()
    cur = conn.cursor()
    frag, params = _build_where(filters)
    cur.execute(_BASE_SQL + frag + f" LIMIT {int(hard_limit)}", params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def _name_score(name_flat, nq):
    """0-100 relevance of a whitespace-stripped campsite name to the query.

    Substring hits win outright (a search for "pinecrest" must surface
    "Pinecrest Campground" first); everything else falls back to rapidfuzz,
    with partial_ratio discounted because on its own it over-matches short
    queries.
    """
    if not name_flat:
        return 0.0
    if nq in name_flat:
        pos = name_flat.index(nq)
        # full name == query -> 100; leading match of a longer name -> ~90;
        # buried match -> lower, but never below 80 (still a real hit).
        return max(80.0, 100.0 - pos * 0.8 - (len(name_flat) - len(nq)) * 0.3)
    if name_flat in nq:
        return 82.0
    return max(
        fuzz.token_set_ratio(name_flat, nq),
        fuzz.token_sort_ratio(name_flat, nq),
        fuzz.partial_ratio(name_flat, nq) * 0.8,
    )


def search_campsites(query=None, *, fuzzthresh=62, limit=200, **filters):
    """Faceted search. Filters run in SQL; an optional text query then scores
    what's left. With no query, results come back sorted by forest then name.

    Accepted filters: is_open, forest, water, toilet ('any'|'flush'|'vault'),
    free_only, fee_max, reservable, camping_type ('dispersed'|'developed'),
    elev_min, elev_max, terrain (list), water_feature (list), activities (list).
    """

    rows = _fetch_filtered(filters)
    if not rows:
        return []

    def _by_forest_then_name(items):
        items.sort(key=lambda x: ((x["forest_name"] or "").lower(), (x["name"] or "").lower()))
        return items

    nq = normalize(query) if query else ""
    if not nq:
        return _by_forest_then_name(rows)[:limit]

    # Forest-name search: the query is a substring of a forest slug and matches
    # more forests than site names (e.g. "stanislaus", "shasta"). Needs >=4
    # chars so short fragments don't hijack a site-name search.
    if len(nq) >= 4:
        forest_hits = [r for r in rows if nq in normalize(r["forest_name"])]
        name_substr = sum(1 for r in rows if nq in normalize(r["name"]))
        if forest_hits and len(forest_hits) > name_substr:
            return _by_forest_then_name(forest_hits)[:limit]

    scored = []
    for row in rows:
        name_score = _name_score(normalize(row["name"]), nq)
        if name_score < fuzzthresh:
            continue
        # Popularity only breaks ties / nudges; the name match still leads.
        total = name_score + _popularity_bonus(row.get("pick_count"))
        scored.append(dict(row, score=total, name_score=name_score))

    scored.sort(key=lambda x: (-x["score"], -(x.get("pick_count") or 0), (x["name"] or "").lower()))
    return scored[:limit]


def get_campsite_by_name(query, fuzzthresh=62, limit=10):
    """Backward-compatible wrapper: text-only search, no facets."""
    return search_campsites(query=query, fuzzthresh=fuzzthresh, limit=limit)


# --- facet option lists (for building the filter UI) ------------------------

def get_facet_options():
    """Distinct values present in the data, for populating filter controls."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT DISTINCT terrain FROM campsites WHERE terrain IS NOT NULL ORDER BY terrain"
    )
    terrains = [r[0] for r in cur.fetchall()]

    cur.execute(
        "SELECT DISTINCT water_feature FROM amenities "
        "WHERE water_feature IS NOT NULL AND water_feature <> 'water nearby' "
        "ORDER BY water_feature"
    )
    water_features = [r[0] for r in cur.fetchall()]

    cur.execute(
        """
        SELECT act, COUNT(*) AS n
        FROM amenities, unnest(activities) AS act
        GROUP BY act
        HAVING COUNT(*) >= 5
        ORDER BY n DESC
        """
    )
    activities = [r[0] for r in cur.fetchall()]

    cur.close()
    conn.close()
    return {
        "terrains": terrains,
        "water_features": water_features,
        "activities": activities,
    }


def get_all_forests():
    """Return a sorted list of distinct forest names from the database.

    This is used to populate the "National Forest" dropdown dynamically so it
    automatically includes every forest present in the data.
    """

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT forest_name
        FROM campsites
        WHERE forest_name IS NOT NULL
        ORDER BY forest_name
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [r[0] for r in rows]


def get_campsites_for_map():
    """Return a lightweight set of campsite data for the map view.

    Limited to campsites that have coordinates and belong to a National
    Forest, matching what's shown in the California reference map.
    """

    conn = get_connection()
    cur = conn.cursor()

    # Some deployments may not have a dedicated forest_name column
    # (older schema used managing_unit instead). Try the newer schema
    # first and gracefully fall back to managing_unit only if needed so
    # the map endpoint never hard-crashes.
    # 'NaN'::numeric guards against the one row with bad coords (a NaN in the
    # JSON payload is invalid to the browser's JSON.parse and kills the map).
    coord_filter = (
        " WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
        " AND latitude <> 'NaN'::numeric AND longitude <> 'NaN'::numeric"
    )
    try:
        cur.execute(
            "SELECT id, name, forest_name, latitude, longitude FROM campsites" + coord_filter
        )
        rows = cur.fetchall()
    except Exception:
        conn.rollback()
        cur.execute(
            "SELECT id, name, managing_unit, latitude, longitude FROM campsites" + coord_filter
        )
        rows = cur.fetchall()

    cur.close()
    conn.close()

    results = []
    for site_id, name, region_label, latitude, longitude in rows:
        lat = float(latitude) if latitude is not None else None
        lon = float(longitude) if longitude is not None else None
        if lat != lat or lon != lon:  # NaN slipped through
            continue
        results.append(
            {
                "id": site_id,
                "name": name,
                "forest_name": region_label,
                "latitude": lat,
                "longitude": lon,
            }
        )

    return results
