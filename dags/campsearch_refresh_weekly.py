"""Weekly full re-scrape — replaces campsearch-refresh-weekly.timer.

run_all.py ran these twelve jobs strictly in series because a bash chain has no
other option. Three of them do not actually depend on each other:
ReserveCalifornia (UseDirect/Tyler), The Dyrt, and the RIDB org ingest all hit
different hosts and write disjoint `source` rows. Fanning them out is the one
real behavioural improvement in this port, not just a translation.

    scrape_fs_usda --all
      └─ scrape_fs_usda --all --detail
           └─ sync_ridb
                └─ ingest_ridb_orgs
                     ├─ scrape_reservecalifornia ─→ scrape_parks_ca ──┐
                     └─ scrape_thedyrt ──────────→ enrich_thedyrt ────┤
                                                                      ▼
      backfill_elevation → derive_attributes → clean_text → geocode_coordless
                                                                      │
                                                                      ▼
                                                        backfill_approx_coords
                                                                      │
                                                                      ▼
                                                             refresh_status
                                                                      │
                                                                      ▼
                                                             refresh_dynamic

Ordering constraints that are real and must not be "optimised" away:
  * fs_usda list before detail — detail backfills coords/fee for rows the list
    tier just created.
  * sync_ridb before ingest_ridb_orgs — match existing sites to facilities
    first, so the org ingest only inserts campgrounds not already held.
  * enrich_thedyrt after scrape_thedyrt — it rebuilds the slug→location-id map
    from the rows the scrape just wrote.
  * scrape_parks_ca after scrape_reservecalifornia — it backfills amenities onto
    RC's rows, which have to exist first.
  * backfill_elevation after every scraper — it fills elevation for *new* coords,
    so it has to see the full week's inserts.
  * derive_attributes before clean_text — derive mines `amenities_raw` and the
    raw `overview`; clean_text then normalises the prose. Reversing them makes
    the regexes miss.
  * geocode_coordless before backfill_approx_coords — try for a real
    coordinate first; only fall back to a county centroid for what is left.
    Reversing them would stamp approximate coords over sites that could have
    had real ones.
  * refresh_status after ingest_ridb_orgs — it only has something to ask about
    once every campsite that is going to get a recreation_facility_id has one.
  * refresh_dynamic last — new campsites need a forecast.

max_active_tasks=3 caps the fan-out. These all write to one Postgres and the
point is a shorter wall clock, not a lock pile-up.
"""

from __future__ import annotations

import pendulum
from airflow.sdk import DAG

from campsearch_common import DEFAULT_ARGS, TZ, campsearch_task

with DAG(
    dag_id="campsearch_refresh_weekly",
    description="Full CampSearch source re-scrape, enrichment and derivation",
    schedule="10 4 * * 0",
    start_date=pendulum.datetime(2026, 9, 16, tz=TZ),
    catchup=False,
    max_active_runs=1,
    max_active_tasks=3,
    default_args=DEFAULT_ARGS,
    dagrun_timeout=pendulum.duration(hours=10),
    tags=["campsearch", "scrape", "weekly"],
    doc_md=__doc__,
):
    fs_usda_list = campsearch_task(
        "scrape_fs_usda_list", "scrape_fs_usda.py", "--all", timeout_minutes=120
    )
    fs_usda_detail = campsearch_task(
        "scrape_fs_usda_detail", "scrape_fs_usda.py", "--all --detail", timeout_minutes=180
    )
    sync_ridb = campsearch_task("sync_ridb", "sync_ridb.py", timeout_minutes=90)
    ingest_ridb_orgs = campsearch_task(
        "ingest_ridb_orgs", "ingest_ridb_orgs.py", "--org all --state CA", timeout_minutes=120
    )

    reserve_california = campsearch_task(
        "scrape_reservecalifornia", "scrape_reservecalifornia.py", timeout_minutes=120
    )
    parks_ca = campsearch_task("scrape_parks_ca", "scrape_parks_ca.py", timeout_minutes=120)

    thedyrt = campsearch_task("scrape_thedyrt", "scrape_thedyrt.py", timeout_minutes=120)
    enrich_thedyrt = campsearch_task("enrich_thedyrt", "enrich_thedyrt.py", timeout_minutes=150)

    backfill_elevation = campsearch_task(
        "backfill_elevation", "backfill_elevation.py", timeout_minutes=120
    )
    derive_attributes = campsearch_task(
        "derive_attributes", "derive_attributes.py", timeout_minutes=60
    )
    clean_text = campsearch_task("clean_text", "clean_text.py", timeout_minutes=60)
    # Nominatim is rate-limited to ~1 req/s and is a free community service.
    geocode_coordless = campsearch_task(
        "geocode_coordless", "geocode_coordless.py", timeout_minutes=120
    )
    backfill_approx_coords = campsearch_task(
        "backfill_approx_coords", "backfill_approx_coords.py", timeout_minutes=30
    )
    refresh_status = campsearch_task(
        "refresh_status", "refresh_status.py", timeout_minutes=180
    )
    refresh_dynamic = campsearch_task(
        "refresh_dynamic",
        "refresh_dynamic.py",
        "--days 3 --max-calls 1200 --sleep 0.3",
        timeout_minutes=120,
        pool="openweather",
    )

    fs_usda_list >> fs_usda_detail >> sync_ridb >> ingest_ridb_orgs

    ingest_ridb_orgs >> reserve_california >> parks_ca
    ingest_ridb_orgs >> thedyrt >> enrich_thedyrt

    [parks_ca, enrich_thedyrt] >> backfill_elevation
    backfill_elevation >> derive_attributes >> clean_text
    clean_text >> geocode_coordless >> backfill_approx_coords
    backfill_approx_coords >> refresh_status >> refresh_dynamic
