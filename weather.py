import requests
from datetime import datetime

# get forecast for ONE day:w
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