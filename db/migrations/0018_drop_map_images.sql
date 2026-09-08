-- 0018_drop_map_images
-- recreation.gov / RIDB facility MEDIA ships the campground layout map as an
-- "Image", so 47 rows in `images` are maps, not photos. 14 of them sorted first
-- and were showing up as the card thumbnail (Tuolumne Meadows, Hobo, ...).
--
-- Delete them. The scrapers (sync_ridb.py, ingest_ridb_orgs.py) now skip any
-- media whose title matches the same pattern via _pipeline.is_map_image, so a
-- re-scrape will not put them back.

DELETE FROM images
WHERE description ~* '\y(maps?|diagram|layout|vicinity)\y'
   OR description ~* 'site ?plan';
