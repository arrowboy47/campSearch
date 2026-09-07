#!/usr/bin/env python3
"""Scrape fs.usda.gov forest "camping & cabins" listings into campsites / status.

Two tiers:

  list tier  (always)  — walk every forest's camping-cabins list pages, upsert
                         each campground by its detail-page URL (the natural key),
                         and write open/closed to status_updates from the teaser
                         badge. Cheap, robust, catches newly added campgrounds.
  detail tier (--detail) — additionally fetch each campground page and best-effort
                         fill latitude/longitude/fee/contact where missing. Every
                         field is independently optional; a parse miss is counted,
                         never fatal.

    python scripts/scrape_fs_usda.py --forest r05/klamath
    python scripts/scrape_fs_usda.py --all
    python scripts/scrape_fs_usda.py --all --detail --force

Replaces scripts/pull_static_info_for_park.py (which dumped CSVs and had no DB
step). HTML selectors verified 2026-09-06; fs.usda.gov redesigns periodically, so
if row counts drop to zero re-check `article.wfs-rec__teaser` and
`.wfs-status .status__heading`.
"""

import re
import time
import random
import argparse

import requests

from _pipeline import get_conn, scrape_run

BASE = "https://www.fs.usda.gov"
LIST_PATH = "/recreation/camping-cabins"
HEADERS = {"User-Agent": "Mozilla/5.0 (campsearch data refresh; contact via github arrowboy47)"}

# Pacific Southwest (R05) + the two out-of-region forests that reach into CA.
ALL_FORESTS = [
    "r05/angeles", "r05/cleveland", "r05/eldorado", "r05/inyo", "r05/klamath",
    "r05/laketahoebasin", "r05/lassen", "r05/lospadres", "r05/mendocino",
    "r05/modoc", "r05/plumas", "r05/sanbernardino", "r05/sequoia",
    "r05/shasta-trinity", "r05/sierra", "r05/sixrivers", "r05/stanislaus",
    "r05/tahoe", "r04/humboldt-toiyabe", "r06/rogue-siskiyou",
]

MAX_LIST_PAGES = 12  # safety valve; largest forest today is 2 pages


def polite_sleep(base=1.2):
    time.sleep(base + random.uniform(0, base))


def forest_slug(forest_path):
    """'r05/shasta-trinity' -> 'shasta-trinity'."""
    return forest_path.rstrip("/").split("/")[-1]


def managing_unit_from_slug(slug):
    return f"{slug.replace('-', ' ').title()} National Forest"


def parse_open(text):
    if not text:
        return None
    t = text.lower()
    if "closed" in t:
        return False
    if "open" in t:
        return True
    return None


WATER_YES = re.compile(r"\b(potable water|drinking water|water is available|has water)\b", re.I)
WATER_NO = re.compile(r"\bno (potable )?water\b|non-?potable", re.I)
RESTROOM_YES = re.compile(r"\b(restroom|vault toilet|flush toilet|toilet)s?\b", re.I)
RESTROOM_NO = re.compile(r"\bno (restroom|toilet)s?\b", re.I)


def infer_amenities(blurb):
    if not blurb:
        return None, None
    water = True if WATER_YES.search(blurb) else (False if WATER_NO.search(blurb) else None)
    restr = True if RESTROOM_YES.search(blurb) else (False if RESTROOM_NO.search(blurb) else None)
    return water, restr


# --- list tier -------------------------------------------------------------

def iter_list_items(session, forest_path):
    """Yield dicts {url, name, is_open, blurb, opportunities} for one forest."""
    from bs4 import BeautifulSoup

    seen = set()
    for page in range(MAX_LIST_PAGES):
        url = f"{BASE}/{forest_path}{LIST_PATH}?items_per_page=50&page=,{page}"
        resp = session.get(url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            break
        soup = BeautifulSoup(resp.text, "html.parser")
        arts = soup.select("div.main-view-item article.wfs-rec__teaser")
        if not arts:
            break

        page_new = 0
        for art in arts:
            a = art.find("a", href=True)
            if not a:
                continue
            href = a["href"]
            full = href if href.startswith("http") else BASE + href
            if full in seen:
                continue
            seen.add(full)
            page_new += 1

            name_el = a.find(["h3", "h2"]) or a
            status_el = art.select_one(".wfs-status .status__heading")
            blurb_el = art.select_one("div.margin-top-2")
            opps = [img.get("title") for img in art.select(".site-details__opportunities img[title]")]

            yield {
                "url": full,
                "name": name_el.get_text(strip=True),
                "is_open": parse_open(status_el.get_text(strip=True) if status_el else None),
                "blurb": blurb_el.get_text(" ", strip=True) if blurb_el else None,
                "opportunities": [o for o in opps if o],
            }

        if page_new == 0:
            break
        polite_sleep(0.8)


UPSERT_CAMPSITE = """
    INSERT INTO campsites (name, forest_name, managing_unit, site_url, fs_usda_url,
                           overview, source, first_seen, last_scraped)
    VALUES (%(name)s, %(forest)s, %(unit)s, %(url)s, %(forest_url)s,
            %(blurb)s, 'fs_usda', now(), now())
    ON CONFLICT (site_url) WHERE site_url IS NOT NULL
    DO UPDATE SET
        name          = EXCLUDED.name,
        managing_unit = COALESCE(campsites.managing_unit, EXCLUDED.managing_unit),
        forest_name   = COALESCE(campsites.forest_name, EXCLUDED.forest_name),
        last_scraped  = now()
    RETURNING id, (xmax = 0) AS inserted;
"""

UPSERT_STATUS = """
    INSERT INTO status_updates (campsite_id, is_open, last_checked, last_updated_site)
    VALUES (%s, %s, now(), now())
    ON CONFLICT (campsite_id)
    DO UPDATE SET is_open = EXCLUDED.is_open, last_checked = now();
"""

UPSERT_AMENITIES = """
    INSERT INTO amenities (campsite_id, water, restrooms, amenities_raw)
    VALUES (%(id)s, %(water)s, %(restrooms)s, %(raw)s)
    ON CONFLICT (campsite_id)
    DO UPDATE SET
        water         = COALESCE(EXCLUDED.water, amenities.water),
        restrooms     = COALESCE(EXCLUDED.restrooms, amenities.restrooms),
        amenities_raw = COALESCE(amenities.amenities_raw, EXCLUDED.amenities_raw);
"""


def run_list_tier(conn, session, forests, run):
    cur = conn.cursor()
    for forest_path in forests:
        slug = forest_slug(forest_path)
        unit = managing_unit_from_slug(slug)
        forest_url = f"{BASE}/{forest_path}"
        for item in iter_list_items(session, forest_path):
            run.seen += 1
            try:
                cur.execute(UPSERT_CAMPSITE, {
                    "name": item["name"], "forest": slug, "unit": unit,
                    "url": item["url"], "forest_url": forest_url,
                    "blurb": item["blurb"],
                })
                camp_id, inserted = cur.fetchone()

                if item["is_open"] is not None:
                    cur.execute(UPSERT_STATUS, (camp_id, item["is_open"]))

                water, restrooms = infer_amenities(item["blurb"])
                raw = ", ".join(item["opportunities"]) or None
                if water is not None or restrooms is not None or raw:
                    cur.execute(UPSERT_AMENITIES, {
                        "id": camp_id, "water": water, "restrooms": restrooms, "raw": raw,
                    })

                conn.commit()
                run.upserted += 1
                if inserted:
                    print(f"  + new: {item['name']} ({slug})")
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                run.errors += 1
                print(f"  ! {item['url']}: {type(exc).__name__}: {exc}")
    cur.close()


# --- detail tier ---------------------------------------------------------------

LAT_RE = re.compile(r"Latitude:\s*(-?\d{1,3}\.\d+)")
LON_RE = re.compile(r"Longitude:\s*(-?\d{1,3}\.\d+)")


def accordion_text(soup, label):
    btn = soup.find("button", string=lambda s: s and label.lower() in s.lower())
    if not btn:
        return None
    head = btn.find_parent(["h2", "h3"])
    body = head.find_next_sibling("div") if head else None
    return body.get_text(" ", strip=True) if body else None


DETAIL_UPDATE = """
    UPDATE campsites SET
        latitude      = COALESCE(%(force_lat)s, latitude, %(lat)s),
        longitude     = COALESCE(%(force_lon)s, longitude, %(lon)s),
        fee           = COALESCE(fee, %(fee)s),
        contact_phone = COALESCE(contact_phone, %(phone)s),
        last_scraped  = now()
    WHERE id = %(id)s;
"""

PHONE_RE = re.compile(r"\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}")


def run_detail_tier(conn, session, run, force, forest_slugs):
    from bs4 import BeautifulSoup

    sel = conn.cursor()
    sel.execute(
        "SELECT id, site_url FROM campsites "
        "WHERE forest_name = ANY(%s) "
        "  AND site_url LIKE 'https://www.fs.usda.gov/%%' "
        "  AND (%s OR latitude IS NULL OR longitude IS NULL) ORDER BY id;",
        (forest_slugs, force),
    )
    targets = sel.fetchall()
    sel.close()

    upd = conn.cursor()
    for camp_id, url in targets:
        run.seen += 1
        try:
            resp = session.get(url, headers=HEADERS, timeout=30)
            if resp.status_code != 200:
                run.errors += 1
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            # Coords render as `<b>Latitude: </b> 41.927`, so match on the
            # collapsed text, not raw HTML.
            page_text = soup.get_text(" ", strip=True)

            lat = LAT_RE.search(page_text)
            lon = LON_RE.search(page_text)
            fee = accordion_text(soup, "Fee Site and Info")
            contact = accordion_text(soup, "Contact Information") or accordion_text(soup, "Office Contact")
            phone = PHONE_RE.search(contact or "")

            upd.execute(DETAIL_UPDATE, {
                "id": camp_id,
                "lat": float(lat.group(1)) if lat else None,
                "lon": float(lon.group(1)) if lon else None,
                "force_lat": float(lat.group(1)) if (force and lat) else None,
                "force_lon": float(lon.group(1)) if (force and lon) else None,
                "fee": (fee or None),
                "phone": phone.group(0) if phone else None,
            })
            conn.commit()
            run.upserted += 1
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            run.errors += 1
            print(f"  ! detail {url}: {type(exc).__name__}: {exc}")
        polite_sleep(1.2)
    upd.close()


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--forest", help="single forest path, e.g. r05/klamath")
    g.add_argument("--all", action="store_true", help="every forest in ALL_FORESTS")
    p.add_argument("--detail", action="store_true", help="also run the detail tier")
    p.add_argument("--force", action="store_true", help="detail tier overwrites existing lat/lon")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    forests = ALL_FORESTS if args.all else [args.forest]
    session = requests.Session()

    with get_conn() as conn, scrape_run("fs_usda") as run:
        run_list_tier(conn, session, forests, run)
        if args.detail:
            run_detail_tier(conn, session, run, args.force,
                            [forest_slug(f) for f in forests])


if __name__ == "__main__":
    main()
