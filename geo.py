"""Geocoding + driving-distance helpers.

Both external services are free / keyless and best-effort: if they fail we fall
back to a straight-line (haversine) estimate rather than erroring out. Results
are cached in-process so repeat lookups during a request are cheap.

  - geocode(address)            -> (lat, lon) | None      via OSM Nominatim
  - driving_distance(a, b)      -> {"miles", "minutes", "estimated"}  via OSRM
  - haversine_miles(a, b)       -> float
  - county_centroid(state, county) -> (lat, lon) | None   from the vendored file
  - effective_coords(row)       -> (lat, lon, is_approx)  real coords preferred
"""

import os
import re
import csv
import math
import time
import functools

import requests

_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_OSRM = "https://router.project-osrm.org/route/v1/driving"
_UA = {"User-Agent": "campsearch (github arrowboy47) - trip distance helper"}
_TIMEOUT = 12


def haversine_miles(a, b):
    """Great-circle distance in miles between (lat, lon) tuples."""
    lat1, lon1 = a
    lat2, lon2 = b
    r = 3958.7613  # earth radius, miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


# Only successful lookups are cached — caching a transient Nominatim
# timeout/429 would permanently reject a valid address for the process lifetime.
_geocode_hits = {}


def geocode(address):
    """Free-text address / city -> (lat, lon), or None if it can't be resolved."""
    address = (address or "").strip()
    if not address:
        return None
    if address in _geocode_hits:
        return _geocode_hits[address]
    try:
        r = requests.get(
            _NOMINATIM,
            params={"q": address, "format": "json", "limit": 1, "countrycodes": "us"},
            headers=_UA,
            timeout=_TIMEOUT,
        )
        time.sleep(1)  # Nominatim asks for <= 1 req/sec
        r.raise_for_status()
        hits = r.json()
        if not hits:
            return None
        result = (float(hits[0]["lat"]), float(hits[0]["lon"]))
        if len(_geocode_hits) < 4096:
            _geocode_hits[address] = result
        return result
    except Exception:
        return None


@functools.lru_cache(maxsize=2048)
def _osrm(o_lat, o_lon, d_lat, d_lon):
    r = requests.get(
        f"{_OSRM}/{o_lon},{o_lat};{d_lon},{d_lat}",
        params={"overview": "false"},
        headers=_UA,
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    route = r.json()["routes"][0]
    return route["distance"], route["duration"]  # metres, seconds


def driving_distance(origin, dest):
    """{'miles', 'minutes', 'estimated'} for driving origin->dest ((lat,lon) each).

    Falls back to haversine * 1.3 (a rough road-vs-crow-flies factor) with
    ``estimated=True`` when the routing service is unavailable.
    """
    if not origin or not dest:
        return None
    try:
        metres, seconds = _osrm(
            round(origin[0], 4), round(origin[1], 4),
            round(dest[0], 4), round(dest[1], 4),
        )
        return {
            "miles": round(metres / 1609.344, 1),
            "minutes": round(seconds / 60),
            "estimated": False,
        }
    except Exception:
        crow = haversine_miles(origin, dest)
        miles = round(crow * 1.3, 1)
        return {
            "miles": miles,
            "minutes": round(miles / 55 * 60),  # ~55 mph average
            "estimated": True,
        }


# --- approximate (county-level) coordinates --------------------------------
#
# For campsites the scrapers found with no real lat/lon. The point is the
# county's Census "internal point" (guaranteed inside the county) — good enough
# for a rough distance or a weather forecast, never for a map marker.

_CENTROID_FILE = os.path.join(os.path.dirname(__file__), "data", "county_centroids.tsv")


def _norm_county(name):
    """'Kern County' / 'st. clair' -> 'kern' / 'st clair' for keying."""
    s = (name or "").lower()
    s = re.sub(r"\bcounty\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


@functools.lru_cache(maxsize=1)
def _centroids():
    """{(state_upper, norm_county): (lat, lon)} from the vendored Census file."""
    out = {}
    try:
        with open(_CENTROID_FILE, newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                try:
                    out[(row["state"].upper(), _norm_county(row["county"]))] = (
                        float(row["lat"]), float(row["lon"])
                    )
                except (ValueError, KeyError):
                    continue
    except FileNotFoundError:
        pass
    return out


def county_centroid(state, county):
    """(lat, lon) for a US county, or None. Keys on state too, so same-named
    counties in different states don't collide."""
    if not state or not county:
        return None
    return _centroids().get((state.upper(), _norm_county(county)))


_COUNTY_RE = re.compile(r"([A-Za-z][A-Za-z .'\-]+?)\s+County,\s*([A-Z]{2})\b")


def parse_county_state(address):
    """Pull ('Kern', 'CA') out of a '..., Kern County, CA' address, or None."""
    if not address:
        return None
    m = _COUNTY_RE.search(address)
    return (m.group(1).strip(), m.group(2)) if m else None


def effective_coords(row):
    """(lat, lon, is_approx) for a campsite dict/row.

    Real latitude/longitude always win. Falls back to approx_latitude/
    approx_longitude (county-level) with is_approx=True. (None, None, False)
    when neither is available.
    """
    def _get(k):
        v = row.get(k) if hasattr(row, "get") else None
        try:
            f = float(v)
            return None if f != f else f  # drop NaN
        except (TypeError, ValueError):
            return None

    lat, lon = _get("latitude"), _get("longitude")
    if lat is not None and lon is not None:
        return lat, lon, False
    lat, lon = _get("approx_latitude"), _get("approx_longitude")
    if lat is not None and lon is not None:
        return lat, lon, True
    return None, None, False
