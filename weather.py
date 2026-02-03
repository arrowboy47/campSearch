import requests
from datetime import datetime
import json

def format_weather_for_display(forecast_json):
    """
    Takes forecast_json (dict or JSON string) and returns a human-readable summary string.
    """
    if not forecast_json:
        return "Weather data unavailable"

    try:
        # If it's a string, parse it
        if isinstance(forecast_json, str):
            forecast = json.loads(forecast_json)
        else:
            forecast = forecast_json

        # Extract data safely
        temp_min = forecast.get("temp_min")
        temp_max = forecast.get("temp_max")
        precip = forecast.get("precipitation_total")

        # Format the output
        parts = []
        if temp_min is not None and temp_max is not None:
            parts.append(f"{int(temp_min)}°F - {int(temp_max)}°F")
        if precip is not None and precip > 0:
            parts.append(f"{precip:.2f}in rain")

        return " | ".join(parts) if parts else "No weather data"

    except Exception:
        return "Weather data unavailable"

# get forecast for ONE day
def get_forecast(lat, lon, date):
    """
    Calls OpenWeather One Call API and returns daily forecast data.
    """
    API_KEY = "7c61c03997ee3bf70f5e685ca593254d"

    url = "https://api.openweathermap.org/data/3.0/onecall/day_summary"
    params = {
        "lat": lat,
        "lon": lon,
        "date": date.strftime("%Y-%m-%d"),
        "units": "imperial",  # or "metric" 
        "appid": API_KEY
    }

    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    # make a dictionary of the desired fields: precipitation, temp min, temp max, cloud cover
    summary = {
        "date": data.get("date"),
        "precipitation_total": data.get("precipitation", {}).get("total"),
        "temp_min": data.get("temperature", {}).get("min"),
        "temp_max": data.get("temperature", {}).get("max"),
        "cloud_cover_afternoon": data.get("cloud_cover", {}).get("afternoon")
    }
    return summary