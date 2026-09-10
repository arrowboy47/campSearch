"""
Heads up to future me: This is my first time making a flask app, so I'm going to overcomment the hell
out of this thing and try to treat comments as like learning tools so i can come back and know what tf going on
"""

from flask import (
    Flask, request, jsonify, render_template, Response,
    session, redirect, url_for, flash, abort, g,
)
from werkzeug.security import generate_password_hash, check_password_hash
from db import (
    get_campsite_by_id, get_trails_for_campsite,
    get_campsites_with_thumbs, record_pick, get_suggested_campsites,
    create_user, get_user, get_user_for_login, update_user_profile,
    export_user_data, save_campsite, unsave_campsite, is_campsite_saved,
    get_saved_campsites,
    create_collection, get_collections, get_collection, delete_collection,
    add_to_collection, remove_from_collection, get_collection_campsites,
)
from weather import get_forecast
from datetime import datetime, timedelta
from functools import wraps
from search import (
    get_campsite_by_name,
    search_campsites,
    get_all_forests,
    get_campsites_for_map,
    get_facet_options,
)
import geo
import json
import re
import os
from forests import forest_label

# Profile-picture uploads land under static/ so Flask can serve them directly.
AVATAR_DIR = os.path.join(os.path.dirname(__file__), "static", "uploads", "avatars")
AVATAR_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
MAX_AVATAR_BYTES = 3 * 1024 * 1024


def save_avatar(file_storage, user_id):
    """Persist an uploaded image as static/uploads/avatars/<user_id>.<ext>.

    Returns the path relative to static/ (for url_for), or None if no file was
    given. Raises ValueError on a bad type or oversize file.
    """
    if not file_storage or not file_storage.filename:
        return None
    ext = AVATAR_EXT.get(file_storage.mimetype)
    if not ext:
        raise ValueError("Profile picture must be a PNG, JPG, WEBP, or GIF.")
    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > MAX_AVATAR_BYTES:
        raise ValueError("Profile picture must be under 3 MB.")

    os.makedirs(AVATAR_DIR, exist_ok=True)
    for old in AVATAR_EXT.values():
        p = os.path.join(AVATAR_DIR, f"{user_id}.{old}")
        if old != ext and os.path.exists(p):
            os.remove(p)
    file_storage.save(os.path.join(AVATAR_DIR, f"{user_id}.{ext}"))
    return f"uploads/avatars/{user_id}.{ext}"


def _int_arg(name):
    raw = request.args.get(name)
    try:
        return int(raw) if raw not in (None, "") else None
    except ValueError:
        return None


def _float_arg(name):
    raw = request.args.get(name)
    try:
        return float(raw) if raw not in (None, "") else None
    except ValueError:
        return None


def parse_search_filters(args):
    """Pull the faceted-search filters out of a request's query string.

    Everything is optional; an unset filter is left out so search_campsites
    doesn't add a WHERE clause for it.
    """

    toilet = args.get("toilet") or None
    camping_type = args.get("camping_type") or None
    return {
        "is_open": args.get("is_open") == "true",
        "forest": args.get("forest") or None,
        "water": args.get("water") == "true",
        "toilet": toilet if toilet in ("any", "flush", "vault") else None,
        "free_only": args.get("free_only") == "true",
        "fee_max": _float_arg("fee_max"),
        "reservable": args.get("reservable") == "true",
        "camping_type": camping_type if camping_type in ("dispersed", "developed") else None,
        "elev_min": _int_arg("elev_min"),
        "elev_max": _int_arg("elev_max"),
        "terrain": args.getlist("terrain") or None,
        "water_feature": args.getlist("water_feature") or None,
        "activities": args.getlist("activities") or None,
    }

# Create Flask app
app = Flask(__name__)

import config as _config  # noqa: E402
app.secret_key = _config.secret_key()


# --- auth plumbing --------------------------------------------------------

def current_user():
    """The logged-in user dict, or None. Cached on `g` for the request."""
    if "user" not in g:
        uid = session.get("user_id")
        g.user = get_user(uid) if uid else None
    return g.user


@app.context_processor
def inject_user():
    # Makes `current_user` available in every template.
    return {"current_user": current_user()}


@app.template_filter("forest")
def _forest_filter(value):
    """`{{ slug | forest }}` -> a readable forest name."""
    return forest_label(value)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Sign in to do that.")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def _effective_origin(device_lat, device_lon):
    """Pick the origin point + label for distance math.

    Default: the user's saved home. If the browser handed us a device location
    and it's more than 10 mi from home (or there's no home), use the device
    location instead. Returns ((lat, lon), label) or (None, None).
    """
    user = current_user()
    home = None
    if user and user.get("home_lat") is not None and user.get("home_lon") is not None:
        home = (user["home_lat"], user["home_lon"])

    device = None
    if device_lat is not None and device_lon is not None:
        device = (device_lat, device_lon)

    if device and (not home or geo.haversine_miles(home, device) > 10):
        return device, "your location"
    if home:
        return home, "home"
    if device:
        return device, "your location"
    return None, None


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
    facets = get_facet_options()

    # "Campsites near you" carousel. Origin = the browser's shared location if
    # it handed one over via ?lat=&lon=, else the signed-in user's saved home.
    origin, label = _effective_origin(_float_arg("lat"), _float_arg("lon"))
    near = []
    if origin:
        rows = get_campsites_with_thumbs()
        for r in rows:
            if r["latitude"] is None or r["longitude"] is None:
                continue
            r["distance_miles"] = round(
                geo.haversine_miles(origin, (r["latitude"], r["longitude"])), 1
            )
        rows.sort(key=lambda x: x.get("distance_miles", 9e9))
        nearest = rows[:40]
        with_pic = [r for r in nearest if r.get("image_url")]
        near = (with_pic or nearest)[:12]

    # "Suggested for you" — only for a signed-in user with enough saved history.
    suggested = []
    user = current_user()
    if user:
        suggested = get_suggested_campsites(user["id"], limit=12)

    return render_template(
        "index.html",
        forests=forests,
        facets=facets,
        near=near,
        near_label="you" if label == "your location" else "home",
        near_prompt=not origin,
        suggested=suggested,
    )

@app.route("/api/campsite/<int:campsite_id>")
def get_campsite(campsite_id):
    data = get_campsite_by_id(campsite_id)
    if not data:
        return jsonify({"error": "Campsite not found"}), 404
    return jsonify(data)

# weather route
# one note the onecall openweather api only does forecasts a year and a half in the future
@app.route("/api/campsite/<int:campsite_id>/pick", methods=["POST"])
def campsite_pick(campsite_id):
    """Record a result-list click-through (migration 0017).

    Fired by a `navigator.sendBeacon` on the results page, so it must be cheap
    and never error. Feeds the popularity bonus in search scoring.
    """
    record_pick(campsite_id)
    return "", 204


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

    lat, lon, coord_is_approx = geo.effective_coords(campsite)
    if lat is None:
        return jsonify({"error": "No location on file for this campsite"}), 422

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
        "approximate": coord_is_approx,
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
    filters = parse_search_filters(request.args)

    try:
        matches = search_campsites(query=query or None, limit=200, **filters)
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
    filters = parse_search_filters(request.args)
    start_str = request.args.get("start")
    end_str = request.args.get("end")

    try:
        campsites = search_campsites(query=query or None, limit=200, **filters)
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
        facets=get_facet_options(),
        filters=filters,
        result_count=len(enriched),
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

    # Real coordinates for anything precise (map, AllTrails box). A county-level
    # approx point only backs the rough distance + weather, and only with a
    # visible "approximate" flag.
    real_lat = campsite_data.get("latitude")
    real_lon = campsite_data.get("longitude")
    lat, lon, coord_is_approx = geo.effective_coords(campsite_data)

    # Build an AllTrails explore URL for hikes in the area using
    # a small bounding box around the campsite coordinates. Real coords only.
    alltrails_url = None
    if real_lat is not None and real_lon is not None:
        lat_f = float(real_lat)
        lon_f = float(real_lon)
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

    # Nearby hikes from AllTrails (empty for campsites not yet harvested; the
    # constructed alltrails_url is the fallback "explore" link in that case).
    trails = get_trails_for_campsite(campsite_id)

    # Driving distance from the user's home (or device location if given and
    # far from home). Computed server-side only when we already have a home on
    # file; otherwise the page's JS offers to use the browser location.
    drive = None
    if lat is not None and lon is not None:
        origin, label = _effective_origin(_float_arg("lat"), _float_arg("lon"))
        if origin:
            dist = geo.driving_distance(origin, (lat, lon))
            if dist:
                dist["label"] = label
                dist["approximate"] = coord_is_approx
                drive = dist

    saved = False
    user_collections = []
    if current_user():
        saved = is_campsite_saved(session["user_id"], campsite_id)
        user_collections = get_collections(session["user_id"])

    return render_template(
        "campsite.html",
        campsite=campsite_data,
        daily_forecast=daily_forecast,
        trails=trails,
        drive=drive,
        saved=saved,
        user_collections=user_collections,
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
        alltrails_url=alltrails_url,
        coord_is_approx=coord_is_approx,
        approx_area=campsite_data.get("address"),
    )

def _slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "campsite").lower()).strip("-") or "campsite"


def build_trip_markdown(camp, trails, forecast, start_str, end_str):
    """A plain-text trip sheet for a campsite: the stuff you'd want offline."""
    lines = [f"# {camp['name']}", ""]

    forest = camp.get("managing_unit") or camp.get("forest_name")
    facts = []
    if forest:
        facts.append(f"- **Forest / unit:** {forest}")
    if camp.get("agency_name"):
        facts.append(f"- **Managed by:** {camp['agency_name']}")
    if camp.get("latitude") is not None and camp.get("longitude") is not None:
        facts.append(f"- **Coordinates:** {camp['latitude']:.5f}, {camp['longitude']:.5f}")
    if camp.get("elevation_ft"):
        facts.append(f"- **Elevation:** {camp['elevation_ft']:,} ft")
    if camp.get("terrain"):
        facts.append(f"- **Terrain:** {camp['terrain']}")
    if camp.get("is_free"):
        facts.append("- **Fee:** Free")
    elif camp.get("fee"):
        facts.append(f"- **Fee:** {camp['fee']}")
    if camp.get("num_sites"):
        facts.append(f"- **Sites:** {camp['num_sites']}")
    if camp.get("has_water"):
        facts.append("- **Water:** drinking water on site")
    if camp.get("toilet_type") and camp["toilet_type"] != "none":
        facts.append(f"- **Toilets:** {camp['toilet_type']}")
    if camp.get("water_feature"):
        facts.append(f"- **Water feature:** {camp['water_feature']}")
    if camp.get("contact_phone"):
        facts.append(f"- **Phone:** {camp['contact_phone']}")
    lines += facts + [""]

    if start_str:
        span = start_str if start_str == end_str else f"{start_str} → {end_str}"
        lines += [f"**Trip dates:** {span}", ""]

    if camp.get("activities"):
        lines += ["## Activities", ", ".join(camp["activities"]), ""]

    if forecast:
        lines += ["## Weather"]
        for day in forecast:
            bits = [day.get("date") or ""]
            if day.get("sky"):
                bits.append(day["sky"])
            if day.get("high") is not None and day.get("low") is not None:
                bits.append(f"high {round(day['high'])}° / low {round(day['low'])}°")
            if day.get("precip_in") is not None:
                bits.append(f"{day['precip_in']:.2f} in precip")
            lines.append(f"- {' · '.join(b for b in bits if b)}")
        lines.append("")

    if camp.get("overview"):
        lines += ["## Overview", camp["overview"].strip(), ""]

    if trails:
        lines += ["## Hikes nearby"]
        for t in trails:
            stat = []
            if t.get("difficulty"):
                stat.append(t["difficulty"])
            if t.get("length_miles") is not None:
                stat.append(f"{t['length_miles']:.1f} mi")
            if t.get("elevation_gain_feet") is not None:
                stat.append(f"{round(t['elevation_gain_feet'])} ft gain")
            if t.get("distance_miles") is not None:
                stat.append(f"{t['distance_miles']:.1f} mi from camp")
            suffix = f" ({', '.join(stat)})" if stat else ""
            link = f"[{t['name']}]({t['url']})" if t.get("url") else t["name"]
            lines.append(f"- {link}{suffix}")
        lines.append("")

    if camp.get("site_url"):
        lines += [f"Official listing: {camp['site_url']}"]

    lines += ["", "_Confirm details with the official listing before traveling._", ""]
    return "\n".join(lines)


@app.route("/campsite/<int:campsite_id>/trip.md")
def campsite_trip_sheet(campsite_id):
    camp = get_campsite_by_id(campsite_id)
    if not camp:
        return "Campsite not found", 404

    start_str = request.args.get("start")
    end_str = request.args.get("end") or start_str

    forecast = []
    lat, lon = camp.get("latitude"), camp.get("longitude")
    if start_str and lat is not None and lon is not None:
        try:
            start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_str, "%Y-%m-%d").date()
            for i in range(max((end_date - start_date).days + 1, 1)):
                summary = build_weather_summary(
                    get_forecast(lat, lon, start_date + timedelta(days=i))
                )
                if summary:
                    forecast.append(summary)
        except Exception:
            forecast = []

    trails = get_trails_for_campsite(campsite_id)
    md = build_trip_markdown(camp, trails, forecast, start_str, end_str)

    filename = f"{_slugify(camp['name'])}-trip.md"
    return Response(
        md,
        mimetype="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- accounts ----------------------------------------------------------------

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user():
        return redirect(url_for("account"))

    if request.method == "POST":
        form = request.form
        username = (form.get("username") or "").strip()
        password = form.get("password") or ""
        errors = []
        if len(username) < 3:
            errors.append("Username must be at least 3 characters.")
        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")

        # Address is optional, but if given it must resolve to a real place —
        # distance-from-home is useless with a bogus address.
        home_address = (form.get("home_address") or "").strip() or None
        home_lat = home_lon = None
        if home_address:
            hit = geo.geocode(home_address)
            if hit:
                home_lat, home_lon = hit
            else:
                errors.append("We couldn't find that home address. Check it, or leave it blank.")

        if errors:
            for e in errors:
                flash(e)
            return render_template("signup.html", form=form)

        user = create_user(
            username,
            generate_password_hash(password),
            first_name=(form.get("first_name") or "").strip() or None,
            last_name=(form.get("last_name") or "").strip() or None,
            email=(form.get("email") or "").strip() or None,
            home_address=home_address,
            home_lat=home_lat,
            home_lon=home_lon,
        )
        if not user:
            flash("That username is taken.")
            return render_template("signup.html", form=form)

        try:
            avatar_path = save_avatar(request.files.get("avatar"), user["id"])
            if avatar_path:
                update_user_profile(user["id"], avatar_path=avatar_path)
        except ValueError as exc:
            flash(str(exc) + " (Account created without a picture.)")

        session.clear()
        session["user_id"] = user["id"]
        flash("Account created.")
        return redirect(url_for("account"))

    return render_template("signup.html", form={})


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("account"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        row = get_user_for_login(username)
        if not row or not check_password_hash(row["password_hash"], password):
            flash("Wrong username or password.")
            return render_template("login.html", username=username)
        session.clear()
        session["user_id"] = row["id"]
        nxt = request.args.get("next") or request.form.get("next")
        return redirect(nxt if nxt and nxt.startswith("/") else url_for("account"))

    return render_template("login.html", username="")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Signed out.")
    return redirect(url_for("home"))


@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    user = current_user()

    if request.method == "POST":
        form = request.form
        fields = {
            "first_name": (form.get("first_name") or "").strip() or None,
            "last_name": (form.get("last_name") or "").strip() or None,
            "email": (form.get("email") or "").strip() or None,
        }
        new_address = (form.get("home_address") or "").strip() or None
        if new_address != user.get("home_address"):
            if new_address:
                hit = geo.geocode(new_address)
                if not hit:
                    flash("We couldn't find that address — home not changed.")
                else:
                    fields["home_address"] = new_address
                    fields["home_lat"], fields["home_lon"] = hit
            else:
                fields["home_address"] = None
                fields["home_lat"] = fields["home_lon"] = None

        try:
            avatar_path = save_avatar(request.files.get("avatar"), user["id"])
            if avatar_path:
                fields["avatar_path"] = avatar_path
        except ValueError as exc:
            flash(str(exc))

        update_user_profile(user["id"], **fields)
        g.pop("user", None)
        flash("Profile updated.")
        return redirect(url_for("account"))

    return render_template(
        "account.html",
        user=user,
        saved=get_saved_campsites(user["id"]),
        collections=get_collections(user["id"]),
        active="saved",
    )


@app.route("/account/export")
@login_required
def account_export():
    data = export_user_data(current_user()["id"])
    payload = json.dumps(data, indent=2, default=str)
    return Response(
        payload,
        mimetype="application/json",
        headers={"Content-Disposition": 'attachment; filename="campsearch-account.json"'},
    )


@app.route("/campsite/<int:campsite_id>/save", methods=["POST"])
@login_required
def toggle_saved(campsite_id):
    if not get_campsite_by_id(campsite_id):
        abort(404)
    uid = current_user()["id"]
    if request.form.get("action") == "unsave":
        unsave_campsite(uid, campsite_id)
    else:
        save_campsite(uid, campsite_id)
    nxt = request.form.get("next")
    return redirect(nxt if nxt and nxt.startswith("/") else url_for("campsite", campsite_id=campsite_id))


# --- collections ---------------------------------------------------------

def _safe_next(default):
    nxt = request.form.get("next")
    return nxt if nxt and nxt.startswith("/") else default


@app.route("/account/collections", methods=["GET", "POST"])
@login_required
def collections_page():
    uid = current_user()["id"]
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Give the collection a name.")
        elif not create_collection(uid, name):
            flash(f"You already have a collection called “{name}”.")
        else:
            flash(f"Created “{name}”.")
        return redirect(url_for("collections_page"))

    return render_template(
        "collections.html",
        collections=get_collections(uid),
        active="collections",
        user=current_user(),
    )


@app.route("/account/collections/<int:collection_id>", methods=["GET", "POST"])
@login_required
def collection_detail(collection_id):
    uid = current_user()["id"]
    coll = get_collection(uid, collection_id)
    if not coll:
        abort(404)

    if request.method == "POST":
        action = request.form.get("action")
        if action == "delete":
            delete_collection(uid, collection_id)
            flash(f"Deleted “{coll['name']}”.")
            return redirect(url_for("collections_page"))
        if action == "remove":
            cid = request.form.get("campsite_id", type=int)
            if cid:
                remove_from_collection(uid, collection_id, cid)
        return redirect(url_for("collection_detail", collection_id=collection_id))

    return render_template(
        "collection_detail.html",
        collection=coll,
        campsites=get_collection_campsites(collection_id),
        collections=get_collections(uid),
        active="collections",
        user=current_user(),
    )


@app.route("/campsite/<int:campsite_id>/collection", methods=["POST"])
@login_required
def campsite_add_to_collection(campsite_id):
    if not get_campsite_by_id(campsite_id):
        abort(404)
    uid = current_user()["id"]
    cid = request.form.get("collection_id", type=int)
    new_name = (request.form.get("new_collection") or "").strip()

    if new_name:
        coll = create_collection(uid, new_name)
        if coll:
            cid = coll["id"]
        else:
            flash(f"You already have a collection called “{new_name}”.")
            cid = None
    if cid:
        add_to_collection(uid, cid, campsite_id)
        flash("Added to collection.")

    return redirect(_safe_next(url_for("campsite", campsite_id=campsite_id)))


# --- campsites near me ------------------------------------------------------

@app.route("/nearby")
def nearby():
    """Campsites closest to the user, by straight-line distance.

    Origin priority: an explicit ?lat=&lon= from the browser's geolocation,
    then the signed-in user's saved home. With neither, the page just explains
    how to get results.
    """
    origin, label = _effective_origin(_float_arg("lat"), _float_arg("lon"))

    ranked = []
    if origin:
        points = get_campsites_for_map()
        for p in points:
            if p["latitude"] is None or p["longitude"] is None:
                continue
            miles = geo.haversine_miles(origin, (p["latitude"], p["longitude"]))
            p["distance_miles"] = round(miles, 1)
            ranked.append(p)
        ranked.sort(key=lambda x: x["distance_miles"])
        ranked = ranked[:60]

    return render_template(
        "nearby.html",
        campsites=ranked,
        origin_label=label,
        has_origin=bool(origin),
    )


@app.route("/api/me")
def api_me():
    """Small bootstrap blob for client JS: the signed-in user's home point
    (for the map's home marker) if there is one."""
    user = current_user()
    home = None
    if user and user.get("home_lat") is not None and user.get("home_lon") is not None:
        home = {"lat": user["home_lat"], "lon": user["home_lon"],
                "label": user.get("home_address")}
    return jsonify({"signed_in": bool(user), "home": home})


@app.route("/api/distance")
def api_distance():
    """Driving distance for one campsite from the effective origin.

    Used by the campsite page's JS to fill in / correct the distance line once
    the browser has shared a device location.
    """
    cid = _int_arg("campsite_id")
    camp = get_campsite_by_id(cid) if cid else None
    if not camp:
        return jsonify({"error": "unknown campsite"}), 404

    dest_lat, dest_lon, coord_is_approx = geo.effective_coords(camp)
    if dest_lat is None:
        return jsonify({"available": False})

    origin, label = _effective_origin(_float_arg("lat"), _float_arg("lon"))
    if not origin:
        return jsonify({"available": False})

    dist = geo.driving_distance(origin, (dest_lat, dest_lon))
    if not dist:
        return jsonify({"available": False})
    dist["available"] = True
    dist["label"] = label
    dist["approximate"] = coord_is_approx
    return jsonify(dist)


# lets see what next
# runs the app and runs the index route by default, I think?
# zsh: p app.py
if __name__ == "__main__":
    app.run(debug=True)
