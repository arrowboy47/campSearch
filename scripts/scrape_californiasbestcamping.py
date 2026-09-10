#!/usr/bin/env python3
"""Scrape californiasbestcamping.com and reconcile it against our campsites.

The site is a hand-maintained guide: one index table (name + county) linking
~730 detail pages, each with a labelled `<th>/<td>` fact table (elevation,
site count, season, fees, amenities, attractions), a prose description, and
often an outbound recreation.gov / fs.usda.gov link.

Matching is deliberately NOT O(pages x campsites). We build three lookup
indexes over our DB in a single pass and probe them in order:

  1. recreation.gov facility id   (exact, from the page's rec.gov link)
  2. normalised fs.usda.gov URL   (exact, from the page's Forest Service link)
  3. normalised campground name   (bucketed; fuzzy-scored only within a bucket)

So the cost is O(pages + campsites + small-per-page), not the cross product.

    python scripts/scrape_californiasbestcamping.py                 # scrape + match report
    python scripts/scrape_californiasbestcamping.py --limit 30
    python scripts/scrape_californiasbestcamping.py --enrich        # fill gaps on matched rows
    python scripts/scrape_californiasbestcamping.py --add-new       # insert unmatched as source='californiasbestcamping'

`--enrich` is COALESCE-only (never overwrites a value we already have; activities
are UNIONed). `--add-new` sites have no coordinates (the site publishes none),
so they won't appear on the map until a later pass geocodes them.
"""

import re
import json
import time
import argparse
import collections
import urllib.parse

import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from _pipeline import get_conn, scrape_run, is_map_image
from clean_text import parse_fee, clean_prose

BASE = "https://www.californiasbestcamping.com"
INDEX_URL = f"{BASE}/guides/campground_index.html"

# --- extraction ------------------------------------------------------------

_NAME_STOPWORDS = {"campground", "campgrounds", "campsites", "campsite", "camp",
                   "group", "the", "a"}


def _name_tokens(name):
    if not name:
        return []
    s = re.sub(r"\(.*?\)", " ", name.lower())
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return [t for t in s.split() if t not in _NAME_STOPWORDS]


def norm_name(name):
    """Blocking key: lowercase, drop a parenthetical, strip campground-ish words."""
    toks = _name_tokens(name)
    return " ".join(toks) or (name or "").strip().lower()


def sorted_name(name):
    """Order-insensitive blocking key ('Bliss SP, D. L.' == 'D.L. Bliss SP')."""
    toks = _name_tokens(name)
    return " ".join(sorted(toks))


def norm_fs_url(url):
    """fs.usda.gov recreation URL -> a stable comparison key (host + path tail)."""
    if not url:
        return None
    m = re.search(r"fs\.usda\.gov/(.+?)/?$", url.split("?")[0].lower())
    if not m:
        return None
    parts = [p for p in m.group(1).split("/") if p and p not in ("recreation",)]
    return "/".join(parts[-3:])


ELEV_RE = re.compile(r"([\d,]+)\s*(?:feet|ft\b|')", re.I)
INT_RE = re.compile(r"\d[\d,]*")
PHONE_RE = re.compile(r"\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}")

_OPERATOR_AGENCY = [
    ("national forest", "US Forest Service"),
    ("forest service", "US Forest Service"),
    ("national park service", "National Park Service"),
    ("national park", "National Park Service"),
    ("bureau of land management", "Bureau of Land Management"),
    ("blm", "Bureau of Land Management"),
    ("california state parks", "California State Parks"),
    ("state park", "California State Parks"),
]

# Keyword -> tag. Kept deliberately tight: bare "trail" and bare "ski" were
# dropped because the site's "Attractions near" blurbs mention trailheads and
# ski resorts 20+ miles away, which inflated hiking / winter-sports tags.
_ACTIVITY_KEYWORDS = [
    ("fishing", "fishing"),
    ("swimming", "swimming"), ("swim beach", "swimming"),
    ("wakeboard", "boating"), ("water ski", "boating"), ("waterski", "boating"),
    ("boating", "boating"), ("boat ramp", "boating"), ("boat launch", "boating"),
    ("sailing", "boating"), ("marina", "boating"),
    ("kayak", "paddling"), ("canoe", "paddling"), ("paddling", "paddling"),
    ("stand-up paddle", "paddling"),
    ("whitewater", "whitewater"), ("white water", "whitewater"),
    ("white-water rafting", "whitewater"), ("river rafting", "whitewater"),
    ("backpacking", "backpacking"), ("wilderness permit", "backpacking"),
    ("mountain biking", "mountain biking"), ("mountain-bike", "mountain biking"),
    ("bicycling", "biking"), ("bike trail", "biking"), ("road cycling", "biking"),
    ("hiking", "hiking"), ("day hikes", "hiking"), ("hiking trails", "hiking"),
    ("nature trail", "hiking"),
    ("horseback", "horseback riding"), ("equestrian", "horseback riding"),
    ("horse corral", "horseback riding"),
    ("off-road", "off-roading"), ("off road vehicle", "off-roading"),
    ("ohv", "off-roading"), ("4x4", "off-roading"), ("4wd", "off-roading"),
    ("cross-country ski", "winter sports"), ("cross country ski", "winter sports"),
    ("snowshoe", "winter sports"), ("snowmobil", "winter sports"),
    ("sledding", "winter sports"), ("winter camping", "winter sports"),
    ("snow play", "winter sports"),
    ("beachcombing", "beach access"), ("sandy beach", "beach access"),
    ("tidepool", "beach access"), ("tide pool", "beach access"),
    ("bird watching", "birding"), ("birdwatching", "birding"), ("birding", "birding"),
    ("wildlife viewing", "wildlife viewing"), ("watch for wildlife", "wildlife viewing"),
    ("wildflower", "wildlife viewing"),
    ("rock climbing", "rock climbing"), ("bouldering", "rock climbing"),
    ("stargazing", "stargazing"), ("dark sky", "stargazing"),
    ("photography", "photography"),
    ("hunting", "hunting"),
    ("picnic area", "picnicking"), ("picnicking", "picnicking"),
]


def _agency_for(text):
    low = (text or "").lower()
    for needle, agency in _OPERATOR_AGENCY:
        if needle in low:
            return agency
    return None


def _activities_from(text):
    low = (text or "").lower()
    out = []
    for needle, tag in _ACTIVITY_KEYWORDS:
        if needle in low and tag not in out:
            out.append(tag)
    return out


def _amenities_from(site_text, cg_text):
    """(toilet_type, water bool|None, restrooms bool|None) from the two rows."""
    blob = f"{site_text or ''} {cg_text or ''}".lower()
    if "flush" in blob:
        toilet = "flush"
    elif "vault" in blob:
        toilet = "vault"
    elif "pit toilet" in blob or "pit toilets" in blob:
        toilet = "vault"
    elif "no toilet" in blob or "no restroom" in blob:
        toilet = "none"
    elif "restroom" in blob or "toilet" in blob:
        toilet = "restrooms"
    else:
        toilet = None

    if any(p in blob for p in ("no water", "no potable", "no piped water",
                               "bring water", "bring your own water", "water is not")):
        water = False
    elif any(p in blob for p in ("tap water", "piped water", "drinking water",
                                 "potable water", "faucet", "water available",
                                 "water spigot", "running water")):
        water = True
    else:
        water = None

    restrooms = True if ("restroom" in blob or "flush" in blob) else None
    return toilet, water, restrooms


def parse_detail(html, url, county=None, region=None):
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find(["h1", "h2"])
    name = h1.get_text(" ", strip=True) if h1 else None
    if name:
        name = re.sub(r"\s+-\s+.*$", "", name).strip()  # "X - Shasta Lake" -> "X"

    # labelled fact table
    facts = {}
    for tr in soup.find_all("tr"):
        th, td = tr.find("th"), tr.find("td")
        if th and td:
            key = th.get_text(" ", strip=True).rstrip(":").strip().lower()
            val = td.get_text(" ", strip=True)
            if key and val and key not in facts:
                facts[key] = val

    def fact(*needles):
        for k, v in facts.items():
            if all(n in k for n in needles):
                return v
        return None

    # prose: <p> blocks before the first <table>
    paras = []
    for el in soup.find_all(["p", "table"]):
        if el.name == "table":
            break
        txt = el.get_text(" ", strip=True)
        if len(txt) > 45 and "californiasbestcamping" not in txt.lower():
            paras.append(txt)
    overview = clean_prose("\n\n".join(paras[:5])) if paras else None

    elev = fact("elevation")
    elev_ft = None
    if elev:
        m = ELEV_RE.search(elev)
        if m:
            elev_ft = int(m.group(1).replace(",", ""))

    sites = fact("number of", "site") or fact("number", "campsite")
    num_sites = None
    if sites:
        m = INT_RE.search(sites)
        if m:
            num_sites = int(m.group(0).replace(",", ""))

    season = (fact("camping season") or fact("open", "closed")
              or fact("season") or fact("open"))

    fee_raw = fact("fees") or fact("fee")
    fee_disp = fmin = fmax = is_free = None
    if fee_raw:
        if fee_raw.strip().lower() in ("none", "no fee", "no charge", "free"):
            fee_disp, fmin, fmax, is_free = "Free", 0.0, 0.0, True
        else:
            fee_disp, fmin, fmax, is_free = parse_fee(fee_raw)

    operator = fact("operated by") or fact("managed by")
    unit_text = " ".join(p for p in paras[:2]) + " " + (operator or "")
    unit = None
    mu = re.search(
        r"([A-Z][A-Za-z'.\-]+(?:\s+[A-Z][A-Za-z'.\-]+){0,4}\s+"
        r"(?:National Forest|National Park|National Monument|National Recreation Area|"
        r"State Park|State Recreation Area|State Beach|State Historic Park|"
        r"Regional Park|County Park|Wilderness))",
        unit_text,
    )
    if mu:
        unit = mu.group(1).strip()

    phone = None
    for v in facts.values():
        pm = PHONE_RE.search(v)
        if pm:
            phone = pm.group(0)
            break

    rec_id = None
    rec_link = None
    fs_url = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(r"recreation\.gov/camping/campgrounds/(\d+)", href)
        if m and not rec_id:
            rec_id, rec_link = m.group(1), href.split("?")[0]
        if "fs.usda.gov" in href and "/recreation/" in href and not fs_url:
            fs_url = href.split("?")[0]

    site_amen = facts.get("campsites")
    cg_amen = facts.get("campground")
    toilet, water, restrooms = _amenities_from(site_amen, cg_amen)

    showers = None
    sv = fact("shower")
    if sv:
        showers = not sv.strip().lower().startswith("no")
    dump = None
    dv = fact("dump station")
    if dv:
        dump = not dv.strip().lower().startswith("no")

    attractions = fact("attractions near") or ""
    activities = _activities_from(
        f"{attractions} {' '.join(paras)} {season or ''} {site_amen or ''} {cg_amen or ''}"
    )

    amen_raw_bits = [b for b in (site_amen, cg_amen,
                                 f"showers: {sv}" if sv else None,
                                 f"dump station: {dv}" if dv else None) if b]

    images = []
    slug = url.rstrip("/").split("/")[-1].replace(".html", "")
    slug_toks = set(re.split(r"[^a-z0-9]+", slug.lower())) - {"", "campground", "camp"}
    for img in soup.find_all("img", src=True):
        src = img["src"]
        if not re.search(r"/photos?\d*/", src) or not src.lower().endswith((".jpg", ".jpeg", ".png")):
            continue
        absu = urllib.parse.urljoin(url, src)
        fname = absu.split("/")[-1].lower()
        # skip panoramas, signage, maps, logos
        if re.search(r"(_pan\d*|_pano|_panorama|_sign|_map|_logo|_banner)\.", fname):
            continue
        stem_toks = set(re.split(r"[^a-z0-9]+", fname))
        # keep only images whose filename shares a real token with the page slug
        if slug_toks & stem_toks and not is_map_image(img.get("alt")):
            if absu not in images:
                images.append(absu)

    return {
        "name": name,
        "county": county,
        "region": region,
        "unit": unit,
        "operator": operator,
        "agency": _agency_for(f"{operator} {unit}"),
        "elevation_ft": elev_ft,
        "num_sites": num_sites,
        "seasons_of_use": season,
        "fee_raw": fee_raw,
        "fee": fee_disp,
        "fee_min": fmin,
        "fee_max": fmax,
        "is_free": is_free,
        "overview": overview,
        "location_text": next((v for k, v in facts.items() if k.endswith("location")), None),
        "contact_phone": phone,
        "rec_facility_id": rec_id,
        "reservation_url": rec_link,
        "fs_usda_url": fs_url,
        "toilet_type": toilet,
        "water": water,
        "restrooms": restrooms,
        "showers": showers,
        "dump_station": dump,
        "amenities_raw": " | ".join(amen_raw_bits) or None,
        "activities": activities,
        "images": images[:8],
        "last_updated": (fact("updates") or "").replace("Last Updated", "").strip() or None,
        "source_url": url,
    }


def fetch_index(session):
    html = session.get(INDEX_URL, timeout=60).text
    soup = BeautifulSoup(html, "html.parser")
    out = []
    seen = set()
    for tr in soup.find_all("tr"):
        a = tr.find("a", href=True)
        if not a or ".html" not in a["href"] or a["href"].startswith("#"):
            continue
        href = a["href"]
        if "/regions/" in href or "/guides/" in href or href.endswith("index.html"):
            continue
        url = urllib.parse.urljoin(INDEX_URL, href)
        if url in seen:
            continue
        seen.add(url)
        tds = tr.find_all("td")
        county = tds[0].get_text(" ", strip=True) if tds else None
        region = href.split("/")[-2] if "/" in href.strip("./") else None
        out.append({"name": a.get_text(" ", strip=True),
                    "county": county, "region": region, "url": url})
    return out


# --- matching ------------------------------------------------------------

def build_indexes(conn):
    cur = conn.cursor()
    by_rec, by_fsurl = {}, {}
    by_name = collections.defaultdict(list)
    by_sorted = collections.defaultdict(list)
    by_unit = collections.defaultdict(list)   # norm(unit) -> [(cid, name), ...]
    meta = {}  # cid -> (norm_name, unit_key)

    cur.execute("""
        SELECT c.id, c.name, c.recreation_facility_id, c.fs_usda_url, c.site_url,
               c.managing_unit, c.forest_name, r.facility_id
        FROM campsites c
        LEFT JOIN reservations r ON r.campsite_id = c.id
    """)
    for cid, name, rec_id, fs_url, site_url, unit, forest, resv_fid in cur.fetchall():
        for fid in (rec_id, resv_fid):
            if fid:
                by_rec.setdefault(str(fid).strip(), cid)
        for u in (fs_url, site_url):
            k = norm_fs_url(u)
            if k:
                by_fsurl.setdefault(k, cid)
        nk = norm_name(name)
        if nk and cid not in by_name[nk]:
            by_name[nk].append(cid)
        sk = sorted_name(name)
        if sk and cid not in by_sorted[sk]:
            by_sorted[sk].append(cid)
        ukey = norm_name(unit or forest or "")
        if ukey:
            by_unit[ukey].append((cid, name))
        meta[cid] = (nk, (unit or forest or "").strip().lower())
    cur.close()
    return by_rec, by_fsurl, by_name, by_sorted, by_unit, meta


def match_record(rec, indexes, names_by_id):
    """-> (list_of_campsite_ids, method, confidence). The list has >1 id only
    when one real campground is split across several DB rows (ReserveCalifornia
    stores site-range sub-facilities)."""
    by_rec, by_fsurl, by_name, by_sorted, by_unit, meta = indexes
    if rec["rec_facility_id"] and rec["rec_facility_id"] in by_rec:
        return [by_rec[rec["rec_facility_id"]]], "rec-id", 100

    k = norm_fs_url(rec["fs_usda_url"])
    if k and k in by_fsurl:
        return [by_fsurl[k]], "fs-url", 100

    for key_fn, index, label in ((norm_name, by_name, "name-unique"),
                                 (sorted_name, by_sorted, "name-sorted")):
        bucket = index.get(key_fn(rec["name"]), [])
        if len(bucket) == 1:
            return [bucket[0]], label, 90
        if len(bucket) > 1:
            # site-range split of one campground: every row has the same
            # normalised name and the same managing unit -> match them all
            nkeys = {meta[c][0] for c in bucket}
            ukeys = {meta[c][1] for c in bucket if meta[c][1]}
            if len(nkeys) == 1 and len(ukeys) <= 1:
                return list(bucket), "name-group", 88
            scored = sorted(
                ((fuzz.WRatio(rec["name"].lower(), (names_by_id.get(cid) or "").lower()), cid)
                 for cid in bucket),
                reverse=True,
            )
            best = scored[0]
            second = scored[1] if len(scored) > 1 else (0, None)
            if best[0] >= 88 and best[0] - second[0] >= 6:
                return [best[1]], "name-fuzzy", best[0]
            return [], "ambiguous", best[0]

    # last chance: the CBC page names a park/forest we know -> fuzzy-match the
    # campground name against just that unit's campsites. Catches the state-park
    # sub-camps whose facility name in our DB differs from the page title.
    ukey = norm_name(rec["unit"] or "")
    if ukey and ukey in by_unit:
        cand = by_unit[ukey]
        scored = sorted(
            ((fuzz.WRatio(norm_name(rec["name"]), norm_name(nm)), cid) for cid, nm in cand),
            reverse=True,
        )
        if scored and scored[0][0] >= 82:
            second = scored[1][0] if len(scored) > 1 else 0
            if scored[0][0] - second >= 5 or scored[0][0] >= 92:
                return [scored[0][1]], "unit-fuzzy", scored[0][0]

    return [], "unmatched", 0


# --- writers -----------------------------------------------------------------

# Gap-fill only: COALESCE / NULLIF so nothing we already have is overwritten.
# `fee` is intentionally NOT enriched — the site's fee prose parses too noisily.
# `elevation_ft` is a no-op fill (every matched row already has one); the
# gross-error correction is a separate opt-in UPDATE below.
ENRICH_CAMPSITE = """
    UPDATE campsites SET
        elevation_ft   = COALESCE(elevation_ft, %(elevation_ft)s),
        num_sites      = COALESCE(NULLIF(num_sites, 0), %(num_sites)s),
        seasons_of_use = COALESCE(NULLIF(seasons_of_use, ''), %(seasons_of_use)s),
        contact_phone  = COALESCE(NULLIF(contact_phone, ''), %(contact_phone)s),
        recreation_facility_id = COALESCE(recreation_facility_id, %(rec_facility_id)s),
        overview       = CASE WHEN overview IS NULL OR length(overview) < 40
                              THEN COALESCE(%(overview)s, overview) ELSE overview END,
        last_scraped   = now()
    WHERE id = %(id)s
"""

# Opt-in corrections (--fix-elevation / --fix-water). Elevation only when our
# stored value is grossly wrong (a DEM-lookup artefact); water only when we have
# an explicit value that disagrees with the page.
FIX_ELEVATION = """
    UPDATE campsites SET elevation_ft = %(elevation_ft)s, last_scraped = now()
    WHERE id = %(id)s
      AND elevation_ft IS NOT NULL AND %(elevation_ft)s IS NOT NULL
      AND abs(elevation_ft - %(elevation_ft)s) >= 2000
"""

FIX_WATER = """
    UPDATE amenities SET water = %(water)s
    WHERE campsite_id = %(id)s
      AND %(water)s IS NOT NULL AND water IS DISTINCT FROM %(water)s
"""

ENRICH_AMENITIES = """
    INSERT INTO amenities (campsite_id, activities, water, restrooms, toilet_type, amenities_raw)
    VALUES (%(id)s, %(acts)s, %(water)s, %(restrooms)s, %(toilet)s, %(raw)s)
    ON CONFLICT (campsite_id) DO UPDATE SET
        activities   = ARRAY(SELECT DISTINCT e
                             FROM unnest(amenities.activities || EXCLUDED.activities) e
                             WHERE e <> ''),
        water        = COALESCE(amenities.water, EXCLUDED.water),
        restrooms    = COALESCE(amenities.restrooms, EXCLUDED.restrooms),
        toilet_type  = COALESCE(amenities.toilet_type, EXCLUDED.toilet_type),
        amenities_raw = COALESCE(NULLIF(amenities.amenities_raw, ''), EXCLUDED.amenities_raw)
"""

INSERT_IMAGE = """
    INSERT INTO images (campsite_id, image_url, description)
    VALUES (%s, %s, %s) ON CONFLICT (campsite_id, image_url) DO NOTHING
"""

INSERT_CAMPSITE = """
    INSERT INTO campsites
        (name, latitude, longitude, seasons_of_use, num_sites, elevation_ft,
         fee, fee_raw, fee_min, fee_max, is_free, overview, contact_phone,
         address, site_url, reservation_url, reservation_type,
         recreation_facility_id, agency_id, source, first_seen, last_scraped)
    VALUES
        (%(name)s, %(latitude)s, %(longitude)s, %(seasons_of_use)s, %(num_sites)s,
         %(elevation_ft)s, %(fee)s, %(fee_raw)s, %(fee_min)s, %(fee_max)s,
         %(is_free)s, %(overview)s, %(contact_phone)s, %(address)s, %(source_url)s,
         %(reservation_url)s, %(reservation_type)s, %(rec_facility_id)s,
         %(agency_id)s, 'californiasbestcamping', now(), now())
    ON CONFLICT (site_url) WHERE site_url IS NOT NULL DO UPDATE SET
        latitude     = COALESCE(campsites.latitude, EXCLUDED.latitude),
        longitude    = COALESCE(campsites.longitude, EXCLUDED.longitude),
        last_scraped = now()
    RETURNING id, (xmax = 0) AS inserted
"""

COUNTY_AGENCY = "County / Regional Parks"


def ridb_coords(session, fid):
    """(lat, lon) for a recreation.gov facility id, or (None, None)."""
    try:
        import config
        r = session.get(f"https://ridb.recreation.gov/api/v1/facilities/{fid}",
                        headers={"apikey": config.ridb_api_key()}, timeout=30)
        d = r.json()
        lat, lon = d.get("FacilityLatitude"), d.get("FacilityLongitude")
        if lat and lon:
            return float(lat), float(lon)
    except Exception:  # noqa: BLE001
        pass
    return None, None


def resolve_coords(session, rec):
    """Best-effort lat/lon for an add-new row: RIDB facility first, then a
    geocode of the directions text / name+county."""
    if rec["rec_facility_id"]:
        lat, lon = ridb_coords(session, rec["rec_facility_id"])
        if lat:
            return lat, lon, "ridb"
    import geo
    for q in (rec.get("location_text"),
              f"{rec['name']}, {rec['county']} County, California" if rec.get("county") else None,
              f"{rec['name']}, California"):
        if not q:
            continue
        hit = geo.geocode(q)
        if hit:
            return hit[0], hit[1], "geocode"
    return None, None, None


def agency_ids(conn):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO agencies (name, level) VALUES (%s, 'local') "
        "ON CONFLICT (name) DO NOTHING",
        (COUNTY_AGENCY,),
    )
    conn.commit()
    cur.execute("SELECT name, id FROM agencies")
    m = {n: i for n, i in cur.fetchall()}
    cur.close()
    return m


# --- runner -----------------------------------------------------------------

_UMBRELLA_RE = re.compile(r"(campgrounds$|\bsp campground\b|state park campground$)", re.I)


def is_umbrella(rec):
    """A park-overview page (lists several campgrounds), not one campground."""
    for key in ("name", "index_name"):
        v = (rec.get(key) or "").lower()
        if v and _UMBRELLA_RE.search(v):
            return True
    return "check " in (rec.get("unit") or "").lower()


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, help="only the first N index entries")
    p.add_argument("--sleep", type=float, default=0.3)
    p.add_argument("--enrich", action="store_true", help="COALESCE-fill matched campsites")
    p.add_argument("--fix-elevation", action="store_true",
                   help="with --enrich: overwrite elevation_ft when ours is >=2000ft off")
    p.add_argument("--fix-water", action="store_true",
                   help="with --enrich: overwrite amenities.water when it disagrees with the page")
    p.add_argument("--add-new", action="store_true",
                   help="insert unmatched (non-umbrella) pages as source='californiasbestcamping'")
    p.add_argument("--out", default=None, help="write every scraped+matched record as JSONL here")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    session = requests.Session()
    session.headers.update({"User-Agent": "campsearch reconcile (californiasbestcamping.com)"})

    entries = fetch_index(session)
    if args.limit:
        entries = entries[: args.limit]
    print(f"{len(entries)} campground pages in the index")

    with get_conn() as conn:
        indexes = build_indexes(conn)
        cur = conn.cursor()
        cur.execute("SELECT id, name FROM campsites")
        names_by_id = dict(cur.fetchall())
        cur.close()
        agmap = agency_ids(conn)

    tally = collections.Counter()
    gaps = collections.Counter()
    fixes = collections.Counter()
    ambiguous, unmatched = [], []
    out_fh = open(args.out, "w") if args.out else None

    write_conn = get_conn() if (args.enrich or args.add_new) else None

    with scrape_run("californiasbestcamping") as run:
        for i, e in enumerate(entries, 1):
            run.seen += 1
            try:
                html = session.get(e["url"], timeout=60).text
            except Exception as exc:  # noqa: BLE001
                run.errors += 1
                print(f"  ! {e['url']}: {exc}")
                continue
            rec = parse_detail(html, e["url"], e["county"], e["region"])
            rec["index_name"] = e["name"]
            if not rec["name"]:
                rec["name"] = e["name"]

            ids, method, conf = match_record(rec, indexes, names_by_id)
            rec["match"] = {"campsite_ids": ids, "method": method, "confidence": conf}
            tally[method] += 1

            if out_fh:
                out_fh.write(json.dumps(rec, default=str) + "\n")

            if method == "ambiguous":
                ambiguous.append((e["name"], e["region"]))
            elif not ids and not is_umbrella(rec):
                unmatched.append((e["name"], e["region"], rec["unit"]))
            elif not ids:
                tally["umbrella"] += 1

            if ids:
                if args.enrich and write_conn:
                    cur = write_conn.cursor()
                    for cid in ids:
                        cur.execute(ENRICH_CAMPSITE, {**rec, "id": cid})
                        if args.fix_elevation and rec["elevation_ft"]:
                            cur.execute(FIX_ELEVATION, {"id": cid, "elevation_ft": rec["elevation_ft"]})
                            fixes["elevation"] += cur.rowcount
                        if args.fix_water and rec["water"] is not None:
                            cur.execute(FIX_WATER, {"id": cid, "water": rec["water"]})
                            fixes["water"] += cur.rowcount
                        if any((rec["activities"], rec["water"] is not None,
                                rec["restrooms"] is not None, rec["toilet_type"])):
                            cur.execute(ENRICH_AMENITIES, {
                                "id": cid, "acts": rec["activities"],
                                "water": rec["water"], "restrooms": rec["restrooms"],
                                "toilet": rec["toilet_type"], "raw": rec["amenities_raw"],
                            })
                        for img in rec["images"]:
                            cur.execute(INSERT_IMAGE, (cid, img, "californiasbestcamping.com"))
                        run.upserted += 1
                    write_conn.commit()
                    cur.close()
                for f in ("elevation_ft", "num_sites", "seasons_of_use", "overview"):
                    if rec[f]:
                        gaps[f] += 1
                if rec["activities"]:
                    gaps["activities"] += 1

            elif args.add_new and write_conn and method == "unmatched" and not is_umbrella(rec):
                lat, lon, csrc = resolve_coords(session, rec)
                cur = write_conn.cursor()
                params = {
                    **rec,
                    "latitude": lat, "longitude": lon,
                    "address": ", ".join(x for x in (
                        rec["location_text"],
                        f"{rec['county']} County" if rec["county"] else None, "CA") if x) or None,
                    "reservation_type": ("reservation" if rec["reservation_url"]
                                         else ("first-come" if "no reservation" in
                                               (rec.get("overview") or "").lower() else None)),
                    "agency_id": agmap.get(rec["agency"]) or agmap.get(COUNTY_AGENCY),
                }
                cur.execute(INSERT_CAMPSITE, params)
                new_id, inserted = cur.fetchone()
                if any((rec["activities"], rec["water"] is not None,
                        rec["restrooms"] is not None, rec["toilet_type"])):
                    cur.execute(ENRICH_AMENITIES, {
                        "id": new_id, "acts": rec["activities"],
                        "water": rec["water"], "restrooms": rec["restrooms"],
                        "toilet": rec["toilet_type"], "raw": rec["amenities_raw"],
                    })
                for img in rec["images"]:
                    cur.execute(INSERT_IMAGE, (new_id, img, "californiasbestcamping.com"))
                write_conn.commit()
                cur.close()
                run.upserted += 1
                tally["added" if inserted else "add-existing"] += 1
                tally[f"coords-{csrc or 'none'}"] += 1

            if i % 50 == 0:
                print(f"  {i}/{len(entries)} ...")
            time.sleep(args.sleep)

    if out_fh:
        out_fh.close()
    if write_conn:
        write_conn.close()

    print("\n--- match summary ---")
    match_methods = ("rec-id", "fs-url", "name-unique", "name-sorted", "name-group",
                     "name-fuzzy", "unit-fuzzy")
    for k in match_methods + ("ambiguous", "umbrella", "unmatched",
                              "added", "add-existing",
                              "coords-ridb", "coords-geocode", "coords-none"):
        if tally[k]:
            print(f"  {k:16} {tally[k]}")
    matched = sum(tally[k] for k in match_methods)
    print(f"  {'MATCHED':16} {matched} / {len(entries)} pages")

    if fixes:
        print("\n--- corrections applied ---")
        for k, v in fixes.items():
            print(f"  {k:16} {v}")

    if not args.enrich:
        print("\n--- enrichment available on matched rows (run --enrich) ---")
        for k, v in gaps.items():
            print(f"  {k:16} {v}")

    if ambiguous:
        print(f"\n--- {len(ambiguous)} ambiguous (name bucket, no confident pick) ---")
        for nm, rg in ambiguous[:40]:
            print(f"  {nm}  [{rg}]")
    if unmatched:
        print(f"\n--- {len(unmatched)} unmatched (candidate new sites; --add-new to insert) ---")
        for nm, rg, unit in unmatched[:60]:
            print(f"  {nm}  [{rg}]  {unit or ''}")


if __name__ == "__main__":
    main()
