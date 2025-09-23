import requests
from datetime import datetime

# TODO: Replace this with your actual API key
API_KEY = "7c61c03997ee3bf70f5e685ca593254d"
r = requests.get(url)
forecast_json= r.json() 
print(forecast_json)


def get_forecast(lat, lon):
    """
    Calls OpenWeather One Call API and returns daily forecast data.
    """
    url = f"https://api.openweathermap.org/data/3.0/onecall?lat={latitude}&lon={longitude}&appid={api_key}&exclude=minutely&units=imperial"

    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    return data["daily"]  # list of daily forecasts