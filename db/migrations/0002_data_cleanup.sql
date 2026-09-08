-- 0002_data_cleanup
-- The static scrape went campsite pages -> pandas DataFrame -> CSV -> DB, and
-- pandas wrote missing values as the literal string 'NaN'. Turn those back into
-- real NULLs, then backfill managing_unit (read by campsite.html) from the
-- forest_name slug where it is missing.

UPDATE campsites SET address        = NULL WHERE address        = 'NaN';
UPDATE campsites SET managing_unit  = NULL WHERE managing_unit  = 'NaN';
UPDATE campsites SET contact_name   = NULL WHERE contact_name   = 'NaN';
UPDATE campsites SET contact_phone  = NULL WHERE contact_phone  = 'NaN';
UPDATE campsites SET seasons_of_use = NULL WHERE seasons_of_use = 'NaN';
UPDATE campsites SET fee            = NULL WHERE fee            = 'NaN';
UPDATE campsites SET overview       = NULL WHERE overview       = 'NaN';
UPDATE campsites SET reservation_type = NULL WHERE reservation_type = 'NaN';

UPDATE amenities SET amenities_raw = NULL WHERE amenities_raw = 'NaN';

-- Backfill a human forest name from the slug: 'shasta-trinity' -> 'Shasta Trinity National Forest'.
-- A handful of slugs (laketahoebasin, humboldt-toiyabe, rogue-siskiyou) come out imperfect;
-- the RIDB sync in Phase 5 overwrites managing_unit with the authoritative RecArea name.
UPDATE campsites
SET managing_unit = initcap(replace(forest_name, '-', ' ')) || ' National Forest'
WHERE managing_unit IS NULL
  AND forest_name IS NOT NULL
  AND btrim(forest_name) <> '';
