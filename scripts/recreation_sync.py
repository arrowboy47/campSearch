import os
import time
import re
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from rapidfuzz import fuzz

API_KEY = os.getenv("RIDB_API_KEY")
BASE_URL = "https://ridb.recreation.gov/api/v1"

DB_CONFIG = {
    "dbname": "camping",
    "user": "arrowboy",
}

HEADERS = {
    "accept": "application/json",
    "apikey": API_KEY
}

RATE_LIMIT_DELAY = 0.4
FUZZ_THRESHOLD = 70


def normalize_name(name):
    if not name:
        return ""
    name = name.lower().strip()
    if name.endswith(" campground"):
        name = name.replace(" campground", "")
    return name


def clean_html(text):
    if not text:
        return None
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def search_facility(name, forest_name=None):
    name = normalize_name(name)
    print(f"Searching for {name}")

    url = f"{BASE_URL}/facilities"
    params = {
        "query": name,
        "limit": 5,
        "offset": 0,
        "full": "true",
        "state": "CA",
        "activity": 9
    }

    r = requests.get(url, headers=HEADERS, params=params)
    time.sleep(RATE_LIMIT_DELAY)

    if r.status_code != 200:
        print(f"Search failed: {name}")
        return None

    results = r.json().get("RECDATA", [])
    if not results:
        return None

    best_match = None
    best_score = 0

    for facility in results:

        if not facility.get("Reservable"):
            continue

        if facility.get("FacilityTypeDescription") != "Campground":
            continue

        if forest_name:
            recareas = facility.get("RECAREA", [])
            recarea_names = [r.get("RecAreaName", "").lower() for r in recareas]
            if not any(forest_name.lower() in r for r in recarea_names):
                continue

        api_name = facility.get("FacilityName", "")
        score = fuzz.token_sort_ratio(
            normalize_name(name),
            normalize_name(api_name)
        )

        if score > best_score:
            best_score = score
            best_match = facility

    if best_score >= FUZZ_THRESHOLD:
        best_match_name = best_match.get("FacilityName", "UNKNOWN")
        best_match_id = best_match.get("FacilityID", "UNKNOWN")

        print(
            f"For campsite '{name}' matched with "
            f"'{best_match_name}' (FacilityID: {best_match_id}) "
            f"with score {best_score}"
        )

        return best_match

    print(f"No strict match for {name} (best score: {best_score})")
    return None


def get_campsite_count(facility_id):
    url = f"{BASE_URL}/facilities/{facility_id}/campsites"
    params = {"limit": 1, "offset": 0}

    r = requests.get(url, headers=HEADERS, params=params)
    time.sleep(RATE_LIMIT_DELAY)

    if r.status_code != 200:
        return None

    return r.json()["METADATA"]["RESULTS"]["TOTAL_COUNT"]


def main():
    if not API_KEY:
        raise ValueError("Set RIDB_API_KEY environment variable")

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT id, name, forest_name
        FROM campsites
    """)
    campsites = cur.fetchall()

    for campsite in campsites:
        campsite_id = campsite["id"]
        name = campsite["name"]
        forest_name = campsite["forest_name"]

        print(f"\nProcessing: {name}")

        facility = search_facility(name, forest_name)
        if not facility:
            print("No valid match found.")
            continue

        facility_id = facility["FacilityID"]

        num_sites = get_campsite_count(facility_id)

        if num_sites and num_sites > 0:
            reservation_url = f"https://www.recreation.gov/camping/campgrounds/{facility_id}"
            print(f"Valid campground with {num_sites} sites")
        else:
            reservation_url = None
            num_sites = 0
            print("Facility exists but no campsite data — reservation_url set to NULL")

        facility_phone = facility.get("FacilityPhone")
        facility_description = clean_html(facility.get("FacilityDescription"))

        cur.execute("""
            UPDATE campsites
            SET recreation_facility_id = %s,
                reservation_url = %s,
                num_sites = %s,
                contact_phone = %s,
                overview = %s
            WHERE id = %s
        """, (
            facility_id,
            reservation_url,
            num_sites,
            facility_phone,
            facility_description,
            campsite_id
        ))

        for media in facility.get("MEDIA", []):
            if media.get("MediaType") != "Image":
                continue

            cur.execute("""
                INSERT INTO images (campsite_id, image_url, description)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (
                campsite_id,
                media.get("URL"),
                media.get("Title")
            ))

        conn.commit()
        print(f"Updated campsite {campsite_id}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()