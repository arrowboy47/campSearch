"""
Heads up to future me: This is my first time making a flask app, so I'm going to overcomment the hell
out of this thing and try to treat comments as like learning tools so i can come back and know what tf going on
"""

from flask import Flask, request, jsonify, render_template
from db import get_campsite_by_id
from weather import get_forecast
from datetime import datetime, timedelta
from search import (
    get_campsite_by_name,
    search_campsites,
    get_all_forests,
    get_campsites_for_map,
)
import json

# Create Flask app
app = Flask(__name__)

def build_weather_summary(forecast):
    """Small helper to turn raw forecast_json into a compact summary for templates.

    Keeping this logic in Python (not Jinja) makes it easier to change the
    stored JSON shape later without touching HTML.
    """
    if not forecast:
        return None

    # If the JSON was stored as text for any reason, try to decode it.
    if isinstance(forecast, str):
        try:
            forecast = json.loads(forecast)
        except Exception:
            return None

    # If we ever decide to store multiple days, treat the first entry as "today".
    if isinstance(forecast, list) and forecast:
        forecast = forecast[0]

    if not isinstance(forecast, dict):
        return None

    date = forecast.get("date")
    temp_min = forecast.get("temp_min")
    temp_max = forecast.get("temp_max")
    precip = forecast.get("precipitation_total")
    clouds = forecast.get("cloud_cover_afternoon")

    # Derive a very small, human-friendly sky description from cloud cover
    sky = None
    try:
        if clouds is not None:
            c = float(clouds)
            if c < 25:
                sky = "mostly clear"
            elif c < 60:
                sky = "partly cloudy"
            else:
                sky = "cloudy"
    except (TypeError, ValueError):
        sky = None

    return {
        "date": date,
        "high": temp_max,
        "low": temp_min,
        "precip_in": precip,
        "sky": sky,
    }


# routes tell the app what to do when a user goes to a certain url
# the index/home is the "root" of the site
@app.route("/")
def home():
    """Render the landing page with a dynamic list of forests.

    We hydrate the "National Forest" dropdown from the database so it
    automatically includes every forest present in the data set.
    """

    forests = get_all_forests()
    return render_template("index.html", forests=forests)

@app.route("/api/campsite/<int:campsite_id>")
def get_campsite(campsite_id):
    data = get_campsite_by_id(campsite_id)
    if not data:
        return jsonify({"error": "Campsite not found"}), 404
    return jsonify(data)

# weather route
# one note the onecall openweather api only does forecasts a year and a half in the future
@app.route("/api/weather")
def weather():
    """
    request.args.get is what will allow you to pass arguments to the site url afther the api by putting a 
    '?' and whatever string was passed to the get function is the text you pass the value to by saying equals
    e.g. https://url/api/weather?site_id=4 
    """
    
    # start and end should be strings in the format YYYY-MM-DD
    site_id = request.args.get("site_id")
    start_str = request.args.get("start")
    end_str = request.args.get("end")

    # Validate site_id
    if not site_id:
        return jsonify({"error": "site_id is required"}), 400
        
    campsite = get_campsite_by_id(site_id)

    if not campsite:
        return jsonify({"error": "Campsite not found"}), 404

    lat = campsite["latitude"]
    lon = campsite["longitude"]

    # Handle and parse start/end dates
    # if no start date is provided, set it to today and end date doesnt matter
    try:
        start_date = datetime.strptime(start_str, "%Y-%m-%d").date() if start_str else datetime.now().date()
        end_date = datetime.strptime(end_str, "%Y-%m-%d").date() if end_str else None
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD."}), 400

    forecast_data = []

    try:
        if not end_date:
            # Only one day requested
            forecast = get_forecast(lat, lon, start_date)
            forecast_data.append(forecast)
        else:
            # loop through the dates and get the weather for each day
            days = (end_date - start_date).days + 1
            for i in range(days):
                current_day = start_date + timedelta(days=i)
                forecast = get_forecast(lat, lon, current_day)
                forecast_data.append(forecast)
    except Exception as e:
        return jsonify({"error": "Failed to fetch weather", "details": str(e)}), 500

    return jsonify({
        "site_id": site_id,
        "lat": lat,
        "lon": lon,
        "forecast": forecast_data
    })

@app.route("/api/search")
def search():
    """API search endpoint used by the homepage search box.

    Filters are applied *first* and then fuzzy text search (if provided)
    runs inside that filtered subset. A text query is optional so users can
    search using only filters.
    """

    query = request.args.get("query") or ""

    # Optional filters coming from the search form
    is_open_flag = request.args.get("is_open") == "true"
    has_water_flag = request.args.get("has_water") == "true"
    has_restrooms_flag = request.args.get("has_restrooms") == "true"
    forest = request.args.get("forest") or None

    try:
        matches = search_campsites(
            query=query or None,
            is_open=is_open_flag if is_open_flag else None,
            has_water=has_water_flag if has_water_flag else None,
            has_restrooms=has_restrooms_flag if has_restrooms_flag else None,
            forest=forest,
            limit=200,
        )
    except Exception as e:
        return jsonify({"error": "Search failed", "details": str(e)}), 500

    if not matches:
        return jsonify({"message": "No matches found"}), 404

    return jsonify(matches)

@app.route("/results")
def results():
    """Server-rendered search results page.

    This uses the same filter-first search pipeline as ``/api/search`` but
    always returns HTML instead of JSON. A text query is optional; users can
    browse using only filters.
    """

    query = request.args.get("query") or ""

    is_open_flag = request.args.get("is_open") == "true"
    has_water_flag = request.args.get("has_water") == "true"
    has_restrooms_flag = request.args.get("has_restrooms") == "true"
    forest = request.args.get("forest") or None
    start_str = request.args.get("start")
    end_str = request.args.get("end")

    try:
        campsites = search_campsites(
            query=query or None,
            is_open=is_open_flag if is_open_flag else None,
            has_water=has_water_flag if has_water_flag else None,
            has_restrooms=has_restrooms_flag if has_restrooms_flag else None,
            forest=forest,
            limit=200,
        )
    except Exception as e:
        return f"Search failed: {e}", 500

    # Attach normalized weather summaries for the template.
    enriched = []
    for camp in campsites:
        raw_forecast = camp.get("forecast") or camp.get("forecast_json")
        camp["forecast_json"] = raw_forecast
        camp["weather_summary"] = build_weather_summary(raw_forecast)
        enriched.append(camp)

    return render_template(
        "results.html",
        query=query,
        campsites=enriched,
        start_date=start_str,
        end_date=end_str,
    )


@app.route("/api/map/campsites")
def map_campsites():
    """Return campsite points used to power the homepage map.

    Limited to campsites that have coordinates and belong to a National
    Forest so the map highlights match the California reference map
    conceptually.
    """

    try:
        sites = get_campsites_for_map()
    except Exception as e:
        return jsonify({"error": "Failed to load map data", "details": str(e)}), 500

    return jsonify(sites)


@app.route("/campsite/<int:campsite_id>")
def campsite(campsite_id):
    campsite_data = get_campsite_by_id(campsite_id)
    if not campsite_data:
        return "Campsite not found", 404

    # Date range may be passed from the search filters or map clicks.
    start_str = request.args.get("start")
    end_str = request.args.get("end")

    today = datetime.now().date()
    try:
        start_date = datetime.strptime(start_str, "%Y-%m-%d").date() if start_str else today
        end_date = datetime.strptime(end_str, "%Y-%m-%d").date() if end_str else start_date
    except ValueError:
        # Fallback to a single-day forecast if parsing fails.
        start_date = today
        end_date = today

    lat = campsite_data.get("latitude")
    lon = campsite_data.get("longitude")

    # Build an AllTrails explore URL for hikes in the area using
    # a small bounding box around the campsite coordinates.
    alltrails_url = None
    if lat is not None and lon is not None:
        lat_f = float(lat)
        lon_f = float(lon)
        offset = 0.01450
        lat1 = lat_f + offset  # top-left latitude
        lng1 = lon_f - offset  # top-left longitude
        lat2 = lat_f - offset  # bottom-right latitude
        lng2 = lon_f + offset  # bottom-right longitude

        alltrails_url = (
            "https://www.alltrails.com/explore"
            f"?b_br_lat={lat2}&b_br_lng={lng2}"
            f"&b_tl_lat={lat1}&b_tl_lng={lng1}"
        )

    daily_forecast = []
    if lat is not None and lon is not None:
        try:
            days = (end_date - start_date).days + 1
            for i in range(max(days, 1)):
                current_day = start_date + timedelta(days=i)
                raw = get_forecast(lat, lon, current_day)
                summary = build_weather_summary(raw)
                if summary:
                    daily_forecast.append(summary)
        except Exception:
            daily_forecast = []

    return render_template(
        "campsite.html",
        campsite=campsite_data,
        daily_forecast=daily_forecast,
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
        alltrails_url=alltrails_url,
    )

# lets see what next
# runs the app and runs the index route by default, I think?
# zsh: p app.py
if __name__ == "__main__":
    app.run(debug=True)
