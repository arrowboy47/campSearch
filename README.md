# CampSearch

California campsite search — a cleaner alternative to hunting across
fs.usda.gov, recreation.gov, ReserveCalifornia and a dozen blogs to answer
"where can I camp this weekend?"

Flask + PostgreSQL, fed by an Airflow-orchestrated ingestion pipeline that
consolidates **~2,600 campsites** from six independent sources into one
searchable, faceted dataset.

---

## Why it exists

The data is public, but it is scattered across agencies that do not talk to each
other and sites that were not built to be searched. The US Forest Service lists
campgrounds as prose pages. Recreation.gov has a real API but only covers
reservable federal facilities. California State Parks sits behind a vendor
booking backend. Dispersed and free camping is largely undocumented outside
community sites.

CampSearch unifies them, normalises the mess, and puts a map and a filter panel
on top.

## Data sources

| Source | Access | What it contributes |
|---|---|---|
| **fs.usda.gov** | HTML scrape | USFS campgrounds; open/closed status, coords, fees |
| **recreation.gov (RIDB)** | REST API (key) | Facility IDs, reservation URLs, photos; NPS/BLM/USFS ingest |
| **ReserveCalifornia** | Undocumented JSON (UseDirect/Tyler) | CA State Park campgrounds and bookable units |
| **The Dyrt** | Undocumented JSON:API | Dispersed / free camping — the gap the official sources miss |
| **OpenWeather** | REST API (key) | Per-site, per-day forecasts |
| **OpenStreetMap (Overpass)** | Public API | Nearby hiking trails |

Plus derived passes with no external source: text normalisation, elevation and
terrain backfill, and regex attribute mining.

## Pipeline

Orchestrated by **Apache Airflow 3**. Three DAGs replace what used to be systemd
timers wrapping `scripts/run_all.py`:

| DAG | Schedule | Work |
|---|---|---|
| `campsearch_refresh_daily` | 03:20 daily | Weather refresh, call-budgeted |
| `campsearch_refresh_weekly` | Sun 04:10 | 14-task full re-scrape, enrich and derive |
| `campsearch_trails_monthly` | 1st, 05:00 | OSM trail ingest |

The weekly DAG runs ReserveCalifornia and The Dyrt **in parallel** — they hit
different hosts and write disjoint rows, so the serial chain a bash script forced
was pure wall-clock cost. Real ordering constraints are documented in the DAG
docstring and enforced as dependencies:

```
scrape_fs_usda --all
  └─ scrape_fs_usda --detail
       └─ sync_ridb
            └─ ingest_ridb_orgs
                 ├─ scrape_reservecalifornia ─→ scrape_parks_ca ──┐
                 └─ scrape_thedyrt ──────────→ enrich_thedyrt ────┤
                                                                  ▼
  backfill_elevation → derive_attributes → clean_text → geocode_coordless
                                     → backfill_approx_coords → refresh_dynamic
```

`catchup=False` throughout: these jobs scrape *current* state, so replaying a
missed interval would re-scrape today N times and burn API budget. The schedule
is a freshness policy, not a partitioning scheme.

## Data quality and observability

The parts that took the most work are the ones that stop bad data reaching the UI:

- **Every job writes a `scrape_runs` row** — source, rows seen, rows upserted,
  error count, status, timestamps. "Did the refresh work, and how much did it
  touch?" is one SQL query rather than a guess. A run killed mid-write leaves a
  stuck `running` row, reclaimed as `orphaned` after 6h.
- **Idempotent upserts keyed on natural keys** (`site_url`, facility id, public
  page URL), so every job is safe to re-run and safe to retry.
- **Raw values are preserved alongside cleaned ones.** `fee` is parsed to a short
  canonical string plus numeric `fee_min`/`fee_max`, but the original prose stays
  in `fee_raw` so the parser can be improved and re-run over history.
- **Accumulate, don't overwrite.** `derive_attributes` UNIONs activities and
  COALESCEs amenity flags so it layers onto structured data from other sources
  instead of clobbering it.
- **Approximate data is labelled as such.** Campsites with no usable coordinates
  get a county-centroid fallback used only for rough distance and weather, always
  flagged in the UI, never rendered as a map pin.
- **Versioned migrations** (`db/migrations/NNNN_*.sql`) applied by
  `scripts/migrate.py` and tracked in a `schema_migrations` table.
- **Failure alerting** via Airflow `on_failure_callback`, plus per-task retries
  with exponential backoff and per-task timeouts.

Defects caught and fixed this way include a single `NaN` latitude that produced
invalid JSON and silently blanked the entire map, and a one-to-many join against
a per-day weather table that fanned 2,440 rows to ~4,250 and pushed real search
matches past the result cap.

## Application

- Faceted search: all filters run in SQL via a dynamic `WHERE` builder, fuzzy
  text scoring on the remainder — camping type, water, toilet type, fee ceiling,
  reservable, open, forest, elevation band, terrain, water feature, activities.
- Leaflet map, per-site weather, campsite detail pages with nearby trails.
- Optional accounts: saved campsites, named collections, profile, JSON export.
  Search never requires signing in.
- Distance from a saved home address or the browser's location, whichever is
  more relevant.

## Stack

Python 3.12 · Flask · PostgreSQL 16 · Apache Airflow 3.3 · Docker Compose ·
psycopg2 · BeautifulSoup · RapidFuzz · Leaflet

## Running locally

```bash
cp .env.example .env          # DATABASE_URL, OPENWEATHER_API_KEY, RIDB_API_KEY, SECRET_KEY
pip install -r requirements.txt
python scripts/migrate.py     # apply migrations
flask --app app run --debug
```

Individual pipeline jobs run standalone, which is also how the Airflow tasks
invoke them:

```bash
cd scripts
python refresh_dynamic.py --limit 3 --sleep 0   # cheap smoke test
python run_all.py weekly                        # full chain, no orchestrator
```

Configuration is read from the environment via `config.py` (a local `.env` is
loaded if present). No secrets live in the repo.
