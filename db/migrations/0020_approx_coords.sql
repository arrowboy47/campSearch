-- 0020_approx_coords
-- Some scraped campgrounds (mostly county / regional parks from
-- californiasbestcamping.com) publish no coordinates. We give those an
-- APPROXIMATE county-level point so distance and weather can still be shown --
-- clearly flagged in the UI, and never used to place a marker on the map.
--
-- Kept in separate columns from the real latitude/longitude so:
--   * map / carousel / nearby queries (WHERE latitude IS NOT NULL) ignore them
--     automatically
--   * if a row later gains real coordinates, code that prefers latitude over
--     approx_latitude picks the real one with no migration needed
--
--   approx_latitude / approx_longitude - county internal point (Census Gazetteer)
--   approx_coord_source                - provenance, e.g. 'county-centroid:CA/Kern'

ALTER TABLE campsites ADD COLUMN IF NOT EXISTS approx_latitude     NUMERIC;
ALTER TABLE campsites ADD COLUMN IF NOT EXISTS approx_longitude    NUMERIC;
ALTER TABLE campsites ADD COLUMN IF NOT EXISTS approx_coord_source TEXT;
