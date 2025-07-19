import pandas as pd
import time
import random
import requests
from bs4 import BeautifulSoup

def get_accordion_text_by_label(soup, label):
    """Finds text content inside an accordion by its button label"""
    button = soup.find("button", string=lambda s: s and label in s)
    if button:
        content_div = button.find_parent("h3").find_next_sibling("div")
        return content_div.get_text(strip=True, separator="\n") if content_div else None
    return None

def scrape_forest_static_data(forest_path):
    page = 0
    site_name = []
    site_url = []
    
    base_url = "https://www.fs.usda.gov"
    rest_of_it = "/recreation/camping-cabins"
    forest_url = f"{base_url}/{forest_path}"
    
    # looping through all the pages for a given forest path 
    # until there are no more pages to scrape
    # saving all the site names and urls in the lists
    while True:  
        full_url = f"{forest_url}{rest_of_it}?items_per_page=50&page=,{page}"
        print(f"Scraping list page {page}: {full_url}")
        r = requests.get(full_url)
        soup = BeautifulSoup(r.text, 'html.parser')

        container = soup.find("div", class_="rows__container")
        if not container:
            print("No more results or container missing.")
            break

        cards = container.find_all("div", class_="main-view-item")
        if not cards:
            break

        for card in cards:
            link_tag = card.find("a")
            if link_tag:
                site_name.append(link_tag.get_text(strip=True))
                site_url.append(base_url + link_tag.get("href"))

        # Check for 'pager-next' button
        pager_next = soup.find("a", class_="usa-pagination__next-page")
        if pager_next:
            page += 1
            time.sleep(random.uniform(1, 2))  # polite delay
        else:
            break
        
    # Grab the park name
    pr = requests.get(forest_url)
    soup = BeautifulSoup(pr.text, "html.parser")
    breadcrumb = soup.find("nav", class_="usa-breadcrumb")
    breadcrumb_items = breadcrumb.find_all("li", class_="usa-breadcrumb__list-item")
    forest_name = breadcrumb_items[-1].get_text(strip=True) if breadcrumb_items else "Unknown"

    # Set up initial DataFrame
    park_name = [forest_name] * len(site_name)
    park_url = [forest_url] * len(site_name)
    df = pd.DataFrame({
        "site_name": site_name,
        "site_url": site_url,
        "park_name": park_name,
        "park_url": park_url
    })

    # Fields to collect
    fields = {
        "season_of_use": [],
        "fee_info": [],
        "contact_info": [],
        "info_center": [],
        "latitude": [],
        "longitude": [],
        "directions": [],
        "restrooms": [],
        "water": [],
        "overview": [],
        "amenities": []
    }

    for i, camp_url in enumerate(df["site_url"]):
        print(f"[{forest_path}] Scraping ({i + 1}/{len(df)}): {camp_url}")
        time.sleep(random.uniform(1, 2))

        try:
            r = requests.get(camp_url)
            soup = BeautifulSoup(r.text, "html.parser")

            # Accordion-based fields
            fields["season_of_use"].append(get_accordion_text_by_label(soup, "Seasons of Use"))
            fields["fee_info"].append(get_accordion_text_by_label(soup, "Fee Site and Info"))
            fields["contact_info"].append(get_accordion_text_by_label(soup, "Contact Information"))
            fields["info_center"].append(get_accordion_text_by_label(soup, "Information Center"))

            # Latitude, Longitude, Directions
            lat, lon, directions = None, None, None
            for block in soup.find_all("div", class_="margin-top-5"):
                h2 = block.find("h2")
                if h2 and "Getting There" in h2.get_text(strip=True):
                    for p in block.find_all("p"):
                        t = p.get_text(strip=True)
                        if "Latitude:" in t: lat = t.replace("Latitude:", "").strip()
                        elif "Longitude:" in t: lon = t.replace("Longitude:", "").strip()
                        elif "From" in t or "Take" in t: directions = t
                    break
            fields["latitude"].append(lat)
            fields["longitude"].append(lon)
            fields["directions"].append(directions)

            # Facility Info
            restrooms, water = None, None
            for block in soup.find_all("div", class_="margin-top-5"):
                h2 = block.find("h2")
                if h2 and "Facility and Amenity Information" in h2.get_text(strip=True):
                    for p in block.find_all("p"):
                        t = p.get_text(strip=True)
                        if "Restroom" in t: restrooms = t
                        elif "water" in t.lower(): water = t
                    break
            fields["restrooms"].append(restrooms)
            fields["water"].append(water)

            # Overview & Amenities
            overview_div = soup.find("div", class_="rec-intro")
            overview_div = overview_div.find("div", class_="field field--name-field-rec-description") if overview_div else None
            overview_text, amenities_text = None, None

            if overview_div:
                for p in overview_div.find_all("p"):
                    t = p.get_text(strip=True)
                    if "Overview" in t and not overview_text:
                        next_p = p.find_next_sibling("p")
                        if next_p: overview_text = next_p.get_text(strip=True)
                    if "Amenities:" in t and not amenities_text:
                        amenities_text = t.replace("Amenities:", "").strip()
                    if overview_text and amenities_text:
                        break

            # General Info fallback for amenities
            if not amenities_text:
                amenities_text = ""
                general_info = soup.find("div", class_="field--name-field-rec-general-info")
                if general_info:
                    for p in general_info.find_all("p"):
                        if "Amenities" in p.text:
                            after = p.get_text(strip=True).replace("Amenities:", "").strip()
                            if after:
                                amenities_text = after
                            else:
                                ul = p.find_next_sibling("ul")
                                if ul:
                                    for li in ul.find_all("li"):
                                        amenities_text += "- " + li.get_text(strip=True) + "\n"
                else:
                    amenities_text = None

            fields["overview"].append(overview_text)
            fields["amenities"].append(amenities_text)

        except Exception as e:
            print(f"Error scraping {camp_url}: {e}")
            for key in fields.keys():
                fields[key].append(None)

    # Add fields to DataFrame
    for col, values in fields.items():
        df[col] = values

    return df


# usable for the scrape_forest_static_data() function
r05_urls = [
    "r05/angeles",
    "r05/klamath", 
    "r05/cleveland",
    "r05/eldorado",
    "r05/sequoia",
    "r05/inyo",
    "r05/klamath",
    "r05/laketahoebasin",
    "r05/lassen",
    "r05/lospadres",
    "r05/mendocino",
    "r05/modoc",
    "r05/plumas",
    "r05/sanbernardino",
    "r05/shasta-trinity",
    "r05/sierra",
    "r05/sixrivers",
    "r05/stanislaus",
    "r05/tahoe"
]

r04_urls = [
    "r04/humboldt-toiyabe"
]

r06_urls = [
    "r06/rogue-siskiyou"
]

# full list of URLs
all_urls = r05_urls + r04_urls + r06_urls

half = len(all_urls) // 2
for i, url in enumerate(all_urls[:half]):
    name = all_urls[i].split("/")[-1]
    df = scrape_forest_static_data(url)
    df.to_csv(f"data/{name}.csv")
