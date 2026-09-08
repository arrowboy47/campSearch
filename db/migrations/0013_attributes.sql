-- 0013_attributes
-- Structured attributes for the search rebuild. Today the only thing populated
-- for every campsite is lat/lon; `amenities` rows exist only for fs_usda sites.
--
-- New columns:
--   campsites.elevation_ft  - metres->feet from a DEM lookup on lat/lon (all sites)
--   campsites.terrain       - coarse band derived from elevation + region
--                             (alpine / subalpine forest / montane forest /
--                              foothills / valley / high desert / coastal)
--   amenities.activities     - normalized tags ('hiking','fishing','swimming',...)
--   amenities.water_feature  - nearby water as text ('lake','river','creek',
--                              'reservoir','ocean') or NULL; replaces the useless
--                              body_of_water boolean for display
--   amenities.toilet_type    - 'flush' | 'vault' | 'none' | NULL
--
-- Also: make sure every campsite has an amenities row so the derive job and the
-- search joins have somewhere to write / read.

ALTER TABLE campsites ADD COLUMN IF NOT EXISTS elevation_ft INTEGER;
ALTER TABLE campsites ADD COLUMN IF NOT EXISTS terrain TEXT;

ALTER TABLE amenities ADD COLUMN IF NOT EXISTS activities TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE amenities ADD COLUMN IF NOT EXISTS water_feature TEXT;
ALTER TABLE amenities ADD COLUMN IF NOT EXISTS toilet_type TEXT;

-- Backfill an empty amenities row for any campsite missing one.
INSERT INTO amenities (campsite_id)
SELECT c.id
FROM campsites c
LEFT JOIN amenities a ON a.campsite_id = c.id
WHERE a.campsite_id IS NULL;

CREATE INDEX IF NOT EXISTS campsites_terrain_idx      ON campsites (terrain);
CREATE INDEX IF NOT EXISTS campsites_elevation_ft_idx ON campsites (elevation_ft);
CREATE INDEX IF NOT EXISTS amenities_activities_gin   ON amenities USING GIN (activities);
