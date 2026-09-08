-- 0010_backfill_fs_usda_agency
-- Campgrounds discovered by scrape_fs_usda after the initial dump never got an
-- agency_id (the script wasn't setting it). Everything fs.usda scrapes is
-- Forest Service land.

UPDATE campsites
SET agency_id = (SELECT id FROM agencies WHERE name = 'US Forest Service')
WHERE source = 'fs_usda' AND agency_id IS NULL;
