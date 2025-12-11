from db import get_connection
from weather import get_forecast
from datetime import datetime
import traceback
import json
import time
import random

"""
- Update the open/closed status of campsites

- Refresh the weather forecast in your weather_forecasts table

- Optionally update availability or last updated timestamps (later)
"""

def update_all_weather_and_status():
    conn = get_connection()
    cur = conn.cursor()

    # Get all campsites
    cur.execute("SELECT id, latitude, longitude FROM campsites;")
    campsites = cur.fetchall()

    for i, (site_id, lat, lon) in enumerate(campsites):
        time.sleep(random.uniform(1, 2))
        try:
            # Update weather 
            forecast = get_forecast(lat, lon, datetime.now())

            # Upsert weather forecast
            cur.execute("""
            INSERT INTO weather_forecasts (campsite_id, forecast_json, last_updated)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (campsite_id) DO UPDATE
            SET forecast_json = EXCLUDED.forecast_json,
            last_updated = CURRENT_TIMESTAMP;
            """, (site_id, json.dumps(forecast)))
            print(f"Updated weather for site {site_id}: {forecast}")


            # Update open/closed status

            is_open = True  # Replace with scraper/API call

            # cur.execute("""
            #     INSERT INTO status_updates (campsite_id, is_open, last_checked)
            #     VALUES (%s, %s, CURRENT_TIMESTAMP)
            #     ON CONFLICT (campsite_id) DO UPDATE
            #     SET is_open = EXCLUDED.is_open,
            #         last_checked = CURRENT_TIMESTAMP;
            # """, (site_id, is_open))

            print(f"Updated site {site_id}")

        except Exception:
            print(f"Error updating site {site_id}")
            traceback.print_exc()
            
        # break loop after 4 updates to test and not overload the database
        if i == 4:
            break

    conn.commit()
    cur.close()
    conn.close()

if __name__ == "__main__":
    update_all_weather_and_status()
