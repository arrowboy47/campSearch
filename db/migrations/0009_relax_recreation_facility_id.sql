-- 0009_relax_recreation_facility_id
-- 0003 made recreation_facility_id partially UNIQUE, assuming one RIDB facility ->
-- one campsite row. Not true: our fs.usda scrape splits some campgrounds into
-- separate rows (e.g. "X Campground" and "X Group Campground") that legitimately
-- map to the same RIDB FacilityID. The unique index made sync_ridb throw on
-- ~25 sites. Drop it; keep a plain index for lookups.

DROP INDEX IF EXISTS campsites_recreation_facility_id_key;

CREATE INDEX IF NOT EXISTS campsites_recreation_facility_id_idx
    ON campsites (recreation_facility_id)
    WHERE recreation_facility_id IS NOT NULL;
