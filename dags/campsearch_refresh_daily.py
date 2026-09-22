"""Daily weather refresh — replaces campsearch-refresh-daily.timer.

One OpenWeather call per (site, day) at ~0.3s. Budgeted to 900 calls so the run
lands in ~20 minutes; leftovers roll over to tomorrow. The 90-minute
execution_timeout exists because a socket with no timeout once hung this job for
20+ minutes before weather.py grew `timeout=30` — the timeout is the backstop
for the next bug of that shape, not for this one.

Held in the `openweather` pool (1 slot) so a manually triggered run can never
race the weekly DAG's weather pass and double-spend the API budget.
"""

from __future__ import annotations

import pendulum
from airflow.sdk import DAG

from campsearch_common import DEFAULT_ARGS, TZ, campsearch_task

with DAG(
    dag_id="campsearch_refresh_daily",
    description="OpenWeather forecast refresh for CampSearch campsites",
    schedule="20 3 * * *",
    start_date=pendulum.datetime(2026, 9, 16, tz=TZ),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    dagrun_timeout=pendulum.duration(hours=2),
    tags=["campsearch", "weather", "daily"],
    doc_md=__doc__,
):
    campsearch_task(
        task_id="refresh_dynamic",
        script="refresh_dynamic.py",
        args="--days 2 --max-calls 900 --sleep 0.3",
        timeout_minutes=90,
        pool="openweather",
    )
