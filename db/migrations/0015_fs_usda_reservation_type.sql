-- 0015_fs_usda_reservation_type
-- Every fs.usda campsite came in with reservation_type NULL (the forest-page
-- scraper never set it), so the campsite page can't show a "reservation" vs
-- "first-come" label for ~1000 sites. Backfill from the signal we do have: a
-- recreation.gov reservation_url means it's reservable; a national-forest
-- campground without one is first-come, first-served.

UPDATE campsites
SET reservation_type = 'reservation'
WHERE source = 'fs_usda'
  AND reservation_type IS NULL
  AND reservation_url IS NOT NULL
  AND reservation_url <> '';

UPDATE campsites
SET reservation_type = 'first-come'
WHERE source = 'fs_usda'
  AND reservation_type IS NULL;
