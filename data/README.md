# data/

Vendored reference datasets (checked in so the pipeline has no runtime download).

## `county_centroids.tsv`

`state<TAB>county<TAB>lat<TAB>lon` — one row per US county (3,222 rows).

- **Source:** US Census Bureau 2023 Gazetteer, counties file
  (`https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_counties_national.zip`).
- `lat`/`lon` are the Census **internal point** (`INTPTLAT`/`INTPTLONG`) — a
  coordinate guaranteed to fall inside the county polygon, not a bounding-box
  centre.
- **Licence:** none. US Government work, not copyrightable (17 U.S.C. §105).
- Used by `scripts/backfill_approx_coords.py` to give campsites with no real
  lat/lon an **approximate** county-level location (distance + weather only,
  never the map). Refresh by re-downloading the Gazetteer every few years.
