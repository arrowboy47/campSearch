"""
Heads up to future me: This is my first time making a flask app, so I'm going to overcomment the hell
out of this thing and try to treat comments as like learning tools so i can come back and know what tf going on
"""

from flask import Flask, request, jsonify, render_template
from db import get_campsite_by_id
# from weather import get_forecast
from datetime import datetime, timedelta

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
@app.route("/api/weather")
def weather():
    """
    request.args.get is what will allow you to pass arguments to the site url afther the api by putting a 
    '?' and whatever string was passed to the get function is the text you pass the value to by saying equals
    e.g. https://url/api/weather?site_id=4 
    """
    site_id = request.args.get("site_id") 
    start_str = request.args.get("start")
    end_str = request.args.get("end")

    # error for if site id not provided
    if not site_id:
        return jsonify({"error": "site_id is required"}), 400

    # error for if site_id doesnt exist
    campsite = get_campsite_by_id(site_id)
    if not campsite:
        return jsonify({"error": "Campsite not found"}), 404

    lat = campsite["latitude"]
    lon = campsite["longitude"]
  
    pass

    # try:
    #     forecast_data = get_forecast(lat, lon)
    # except Exception as e:
    #     return jsonify({"error": "Failed to fetch weather", "details": str(e)}), 500

    # # Handle date range
    # today = datetime.utcnow().date()
    # default_start = today + timedelta(days=1)
    # default_end = default_start

    # try:
    #     start = datetime.strptime(start_str, "%Y-%m-%d").date() if start_str else default_start
    #     end = datetime.strptime(end_str, "%Y-%m-%d").date() if end_str else default_end
    # except ValueError:
    #     return jsonify({"error": "Invalid date format. Use YYYY-MM-DD."}), 400

    # # Filter forecast data by date range
    # results = []
    # for day in forecast_data:
    #     dt = datetime.utcfromtimestamp(day["dt"]).date()
    #     if start <= dt <= end:
    #         results.append({
    #             "date": dt.isoformat(),
    #             "summary": day["weather"][0]["description"].title(),
    #             "temp_min": day["temp"]["min"],
    #             "temp_max": day["temp"]["max"],
    #             "precip_chance": day.get("pop", 0)  # probability of precipitation
    #         })

    # return jsonify({
    #     "site_id": site_id,
    #     "lat": lat,
    #     "lon": lon,
    #     "forecast": results
    # })




# runs the app and runs the index route by default, I think?
# bash: p app.py
if __name__ == "__main__":
    app.run(debug=True)