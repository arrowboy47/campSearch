"""
Heads up to future me: This is my first time making a flask app, so I'm going to overcomment the hell
out of this thing and try to treat comments as like learning tools so i can come back and know what tf going on
"""

from flask import Flask, request, jsonify, render_template
from db import get_campsite_by_id
from weather import get_forecast
from datetime import datetime, timedelta
from search import get_campsite_by_name

# Create Flask app
app = Flask(__name__)

# routes tell the app what to do when a user goes to a certain url
# the index/home is the "root" of the site
@app.route("/")
def home():
    return render_template("index.html") # render_template is a function that opens a html file and renders it

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
    '''
    Return a list of N campsites that match the campsite_name
    '''
    query = request.args.get("query")

    if not query:
        return jsonify({"error": "Missing search query"}), 400

    try:
        matches = get_campsite_by_name(query)
    except Exception as e:
        return jsonify({"error": "Search failed", "details": str(e)}), 500

    if not matches:
        return jsonify({"message": "No matches found"}), 404

    return jsonify(matches)

# first non-api route
@app.route("/results")
def results():
    query = request.args.get("query")
    # check if query is in the url
    if not query:
        return "No query provided", 400
    
    matches = get_campsite_by_name(query)
    return render_template("results.html", query=query, campsites=matches)

@app.route("/campsite/<int:campsite_id>")
def campsite(campsite_id):
    campsite_data = get_campsite_by_id(campsite_id)
    if not campsite_data:
        return "Campsite not found", 404

    return render_template("campsite.html", campsite=campsite_data)

# lets see what next
# runs the app and runs the index route by default, I think?
# zsh: p app.py
if __name__ == "__main__":
    app.run(debug=True)
