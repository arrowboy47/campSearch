import requests

from config import openweather_api_key

# get forecast for ONE day
def get_forecast(lat, lon, date):
    """
    Calls OpenWeather One Call API and returns daily forecast data.

    Returns None when there is no location to query (callers that show weather
    for coordinate-less campsites rely on this instead of a 500).
    """
    if lat is None or lon is None:
        return None

    url = "https://api.openweathermap.org/data/3.0/onecall/day_summary"
    params = {
        "lat": lat,
        "lon": lon,
        "date": date.strftime("%Y-%m-%d"),
        "units": "imperial",  # or "metric"
        "appid": openweather_api_key(),
    }

    # Always time out: without this a stalled connection hangs the whole
    # refresh loop indefinitely (seen in the 2026-09 nightly run).
    response = requests.get(url, params=params, timeout=30)
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