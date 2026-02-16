from rapidfuzz import fuzz
from db import get_connection
import re


def normalize(text):
    """Lowercase and strip whitespace for fuzzy matching.

    NOTE: we intentionally remove *all* whitespace here so that queries like
    "pine crest" will still match "Pinecrest" and similar variations.
    """

    if text is None:
        return ""
    return re.sub(r"\s+", "", text.strip().lower())


def _fetch_filtered_campsites(is_open=None, has_water=None, has_restrooms=None, forest=None):
    """Return a list of campsite rows after applying DB-level filters.

    This lets us apply filters *first* in SQL and then run fuzzy search only
    on the already-filtered subset, which is what the frontend expects.
    """

    conn = get_connection()
    cur = conn.cursor()

    sql = """
        SELECT
            campsites.id,
            campsites.name,
            campsites.forest_name,
            campsites.latitude,
            campsites.longitude,
            status_updates.is_open,
            weather_forecasts.forecast_json,
            amenities.water,
            amenities.restrooms
        FROM campsites
        LEFT JOIN status_updates ON campsites.id = status_updates.campsite_id
        LEFT JOIN weather_forecasts ON campsites.id = weather_forecasts.campsite_id
        LEFT JOIN amenities ON campsites.id = amenities.campsite_id
        WHERE 1 = 1
    """

    params = []

    # Only add filters when the corresponding checkbox was selected.
    if is_open is True:
        sql += " AND status_updates.is_open = TRUE"

    if has_water is True:
        sql += " AND amenities.water = TRUE"

    if has_restrooms is True:
        sql += " AND amenities.restrooms = TRUE"

    if forest:
        # Case-insensitive substring match so short values like "stanislaus"
        # still match "Stanislaus National Forest".
        sql += " AND LOWER(campsites.forest_name) LIKE %s"
        params.append(f"%{forest.strip().lower()}%")

    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def search_campsites(
    query=None,
    *,
    is_open=None,
    has_water=None,
    has_restrooms=None,
    forest=None,
    fuzzthresh=40,
    limit=200,
):
    """Search campsites with optional filters and fuzzy matching.

    Filters are applied *first* at the SQL level, then fuzzy search is applied
    within that subset (if a text query is provided).

    If ``query`` is empty/None, this returns all filtered campsites without
    fuzzy scoring, sorted by name.
    """

    rows = _fetch_filtered_campsites(
        is_open=is_open,
        has_water=has_water,
        has_restrooms=has_restrooms,
        forest=forest,
    )

    if not rows:
        return []

    if not query:
        # No text search: just map the rows into dictionaries and sort by name.
        results = []
        for (
            site_id,
            name,
            forest_name,
            latitude,
            longitude,
            is_open_val,
            forecast_json,
            water,
            restrooms,
        ) in rows:
            results.append(
                {
                    "id": site_id,
                    "name": name,
                    "forest_name": forest_name,
                    "latitude": latitude,
                    "longitude": longitude,
                    "is_open": is_open_val,
                    "forecast": forecast_json,
                    "has_water": bool(water) if water is not None else False,
                    "has_restrooms": bool(restrooms) if restrooms is not None else False,
                }
            )

        results.sort(key=lambda x: (x["forest_name"] or "", x["name"]))
        return results[:limit]

    # --- Fuzzy search path (query provided) ---
    norm_query = normalize(query)

    name_results = []
    forest_scores = {}
    best_site_score = 0
    best_forest_score = 0

    for (
        site_id,
        name,
        forest_name,
        latitude,
        longitude,
        is_open_val,
        forecast_json,
        water,
        restrooms,
    ) in rows:
        name_flat = normalize(name)
        forest_flat = normalize(forest_name)

        # Name scores
        token_score = fuzz.token_sort_ratio(name_flat, norm_query)
        partial_score = fuzz.partial_ratio(name_flat, norm_query)
        # Adjust partial score based on query length so very short queries
        # don't dominate long names.
        if len(name_flat) > 0:
            partial_adjusted_score = partial_score * (len(norm_query) / len(name_flat))
        else:
            partial_adjusted_score = partial_score

        site_score = max(token_score, partial_adjusted_score)
        best_site_score = max(best_site_score, site_score)

        # Forest name scores
        partial_forest_score = fuzz.partial_ratio(norm_query, forest_flat) * 0.6
        exact_forest_substring = int(norm_query in forest_flat) * 100
        forest_score = max(partial_forest_score, exact_forest_substring)
        best_forest_score = max(best_forest_score, forest_score)

        # Store top site matches
        if site_score == 100 and forest_score != 100:
            return [
                {
                    "id": site_id,
                    "name": name,
                    "forest_name": forest_name,
                    "latitude": latitude,
                    "longitude": longitude,
                    "score": site_score,
                    "is_open": is_open_val,
                    "forecast": forecast_json,
                    "has_water": bool(water) if water is not None else False,
                    "has_restrooms": bool(restrooms) if restrooms is not None else False,
                }
            ]

        # Store site matches
        if site_score >= fuzzthresh:
            name_results.append(
                {
                    "id": site_id,
                    "name": name,
                    "forest_name": forest_name,
                    "latitude": latitude,
                    "longitude": longitude,
                    "score": site_score,
                    "is_open": is_open_val,
                    "forecast": forecast_json,
                    "has_water": bool(water) if water is not None else False,
                    "has_restrooms": bool(restrooms) if restrooms is not None else False,
                }
            )

        # Collect potential forest matches
        if forest_score >= fuzzthresh:
            forest_scores.setdefault(forest_name, []).append(
                (
                    site_id,
                    name,
                    latitude,
                    longitude,
                    site_score,
                    is_open_val,
                    forecast_json,
                    water,
                    restrooms,
                )
            )

    # Decide if this is primarily a forest search.
    if best_forest_score >= fuzzthresh and best_forest_score > best_site_score:
        best_forest_match = max(
            forest_scores.items(),
            key=lambda item: fuzz.partial_ratio(norm_query, normalize(item[0])),
            default=None,
        )

        if best_forest_match:
            forest_name, site_list = best_forest_match
            sorted_sites = sorted(site_list, key=lambda x: x[4], reverse=True)
            return [
                {
                    "id": sid,
                    "name": sname,
                    "forest_name": forest_name,
                    "latitude": lat,
                    "longitude": lon,
                    "score": score,
                    "is_open": is_open_val,
                    "forecast": forecast_json,
                    "has_water": bool(water) if water is not None else False,
                    "has_restrooms": bool(restrooms) if restrooms is not None else False,
                }
                for (
                    sid,
                    sname,
                    lat,
                    lon,
                    score,
                    is_open_val,
                    forecast_json,
                    water,
                    restrooms,
                ) in sorted_sites
            ][:limit]

    # Otherwise, return top site matches.
    name_results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return name_results[:limit]


def get_campsite_by_name(query, fuzzthresh=40, limit=10):
    """Backward-compatible wrapper around :func:`search_campsites`.

    Existing code that only passes a query can keep using this, but all new
    call sites should prefer ``search_campsites`` so they can take advantage
    of filter-first behavior.
    """

    return search_campsites(query=query, fuzzthresh=fuzzthresh, limit=limit)


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
    try:
        cur.execute(
            """
            SELECT
                id,
                name,
                forest_name,
                latitude,
                longitude
            FROM campsites
            WHERE latitude IS NOT NULL
              AND longitude IS NOT NULL
            """
        )
        rows = cur.fetchall()
        rows_mode = "forest_name"
    except Exception:
        # Roll back the failed statement and fall back to managing_unit.
        conn.rollback()
        cur.execute(
            """
            SELECT
                id,
                name,
                managing_unit,
                latitude,
                longitude
            FROM campsites
            WHERE latitude IS NOT NULL
              AND longitude IS NOT NULL
            """
        )
        rows = cur.fetchall()
        rows_mode = "managing_unit"

    cur.close()
    conn.close()

    results = []
    for site_id, name, region_label, latitude, longitude in rows:
        results.append(
            {
                "id": site_id,
                "name": name,
                # Prefer the explicit forest_name when present; otherwise
                # use managing_unit as the region label.
                "forest_name": region_label,
                "latitude": float(latitude) if latitude is not None else None,
                "longitude": float(longitude) if longitude is not None else None,
            }
        )

    return results
