"""Monthly OSM trail ingest — replaces the run_all.py `monthly` mode.

Overpass is a free, shared, community-funded endpoint. `--sleep 1.5` and a
once-a-month cadence are politeness, not performance tuning: trails move on a
timescale of years, so anything more frequent is taking capacity from other
users for no benefit. Run takes 45-90 minutes for the non-dispersed set.

The AllTrails harvest deliberately stays OUT of Airflow — its MCP connector auth
is session-scoped and cannot be held by a daemon, so it remains a manual,
few-times-a-year job. Same reasoning as under systemd.
"""

from __future__ import annotations

import pendulum
from airflow.sdk import DAG

from campsearch_common import DEFAULT_ARGS, TZ, campsearch_task

with DAG(
    dag_id="campsearch_trails_monthly",
    description="OpenStreetMap Overpass trail ingest for CampSearch",
    schedule="0 5 1 * *",
    start_date=pendulum.datetime(2026, 9, 16, tz=TZ),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    dagrun_timeout=pendulum.duration(hours=4),
    tags=["campsearch", "trails", "monthly"],
    doc_md=__doc__,
):
    campsearch_task(
        task_id="ingest_trails_osm",
        script="ingest_trails_osm.py",
        args="--sleep 1.5",
        timeout_minutes=180,
    )
