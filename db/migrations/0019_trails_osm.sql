-- 0019_trails_osm
-- The AllTrails MCP harvest is a manual per-session grind, so trail coverage
-- stalled at a proof batch. OpenStreetMap (Overpass API) is keyless, openly
-- licensed, and scriptable, so `ingest_trails_osm.py` fills the bulk of the
-- "nearby hikes" data from OSM `route=hiking` relations and named paths.
--
-- Both sources share the `trails` / `campsite_trails` tables:
--   trails.source     - 'alltrails' (existing rows) | 'osm'
--   trails.osm_type    - 'relation' | 'way' for OSM rows, NULL for AllTrails
-- OSM ids are namespaced into trails.id so they can't collide with AllTrails
-- ids: relation -> 300000000000 + id, way -> 200000000000 + id.

ALTER TABLE trails ADD COLUMN IF NOT EXISTS source   TEXT NOT NULL DEFAULT 'alltrails';
ALTER TABLE trails ADD COLUMN IF NOT EXISTS osm_type TEXT;

CREATE INDEX IF NOT EXISTS trails_source_idx ON trails (source);
