#!/usr/bin/env python3
"""Refresh per-day weather (and, later, open/closed status) for campsites.

This is the recurring freshness job — meant for a daily cron. It is idempotent:
a `(campsite_id, forecast_date)` row that is already fresh is left alone, so
re-running within the day is cheap and safe.

    python scripts/refresh_dynamic.py                 # today, all sites w/ coords
    python scripts/refresh_dynamic.py --days 5        # today .. today+4
    python scripts/refresh_dynamic.py --limit 10      # first 10 sites (testing)
    python scripts/refresh_dynamic.py --site-id 759   # one site
    python scripts/refresh_dynamic.py --max-age-hours 6

Replaces the old scripts/dynamic.py, which broke out of its loop after 5 sites
and upserted on the wrong key.
"""

import json
import time
import random
import argparse
import datetime as dt

# _pipeline puts the repo root on sys.path, so `weather` imports after it.
from _pipeline import get_conn, scrape_run  # noqa: E402
from weather import get_forecast  # noqa: E402


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", type=int, default=1, help="number of days from today (default 1)")
    p.add_argument("--limit", type=int, default=None, help="cap number of sites")
    p.add_argument("--site-id", type=int, default=None, help="only this campsite id")
    p.add_argument(
        "--max-age-hours",
        type=float,
        default=12.0,
        help="skip a day whose cached row is younger than this (default 12)",
    )
    p.add_argument("--sleep", type=float, default=0.7,
                   help="base seconds between API calls (actual = base + rand[0,base])")
    p.add_argument("--max-calls", type=int, default=None,
                   help="stop after this many API calls; the rest wait for the next run")
    return p.parse_args(argv)


UPSERT = """
    INSERT INTO weather_forecasts (campsite_id, forecast_date, forecast_json, last_updated)
    VALUES (%s, %s, %s, now())
    ON CONFLICT (campsite_id, forecast_date)
    DO UPDATE SET forecast_json = EXCLUDED.forecast_json,
                  last_updated  = now();
"""

FRESH = """
    SELECT 1 FROM weather_forecasts
    WHERE campsite_id = %s AND forecast_date = %s
      AND last_updated > now() - (%s || ' hours')::interval;
"""


def main(argv=None):
    args = parse_args(argv)
    today = dt.date.today()
    dates = [today + dt.timedelta(days=i) for i in range(max(args.days, 1))]

    with get_conn() as conn:
        sel = conn.cursor()
        if args.site_id is not None:
            sel.execute(
                "SELECT id, latitude, longitude FROM campsites "
                "WHERE id = %s AND latitude IS NOT NULL AND longitude IS NOT NULL;",
                (args.site_id,),
            )
        else:
            q = (
                "SELECT id, latitude, longitude FROM campsites "
                "WHERE latitude IS NOT NULL AND longitude IS NOT NULL ORDER BY id"
            )
            if args.limit:
                q += f" LIMIT {int(args.limit)}"
            sel.execute(q)
        sites = sel.fetchall()
        sel.close()

        with scrape_run("weather") as run:
            work = conn.cursor()
            calls = 0
            stopped_early = False
            for site_id, lat, lon in sites:
                if stopped_early:
                    break
                for day in dates:
                    run.seen += 1
                    work.execute(FRESH, (site_id, day, args.max_age_hours))
                    if work.fetchone():
                        continue
                    if args.max_calls is not None and calls >= args.max_calls:
                        run.note = f"hit --max-calls {args.max_calls}; rest deferred"
                        stopped_early = True
                        break
                    try:
                        forecast = get_forecast(lat, lon, day)
                        calls += 1
                        work.execute(UPSERT, (site_id, day, json.dumps(forecast)))
                        run.upserted += 1
                        conn.commit()
                    except Exception as exc:  # noqa: BLE001
                        calls += 1
                        conn.rollback()
                        run.errors += 1
                        print(f"  site {site_id} {day}: {type(exc).__name__}: {exc}")
                    time.sleep(args.sleep + random.uniform(0, args.sleep))
            work.close()
            if stopped_early:
                print(f"  stopped after {calls} calls (--max-calls)")

    # TODO(2026-09): open/closed status has no real source yet. When one exists
    # (forest alerts feed / recreation.gov), upsert into status_updates here on
    # ON CONFLICT (campsite_id). Do NOT write a hardcoded is_open like the old
    # dynamic.py did.


if __name__ == "__main__":
    main()
