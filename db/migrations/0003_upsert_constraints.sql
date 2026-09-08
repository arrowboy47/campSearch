-- 0003_upsert_constraints
-- Add the UNIQUE constraints and columns the scraper rewrites (Phase 5) need for
-- INSERT ... ON CONFLICT ... DO UPDATE, plus provenance columns on campsites.

-- amenities: collapse to one row per campsite (keep the newest), then UNIQUE.
DELETE FROM amenities a
USING amenities b
WHERE a.campsite_id = b.campsite_id
  AND a.id < b.id;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'amenities_campsite_id_key') THEN
        ALTER TABLE amenities ADD CONSTRAINT amenities_campsite_id_key UNIQUE (campsite_id);
    END IF;
END $$;

-- status_updates: one row per campsite (table is currently empty).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'status_updates_campsite_id_key') THEN
        ALTER TABLE status_updates ADD CONSTRAINT status_updates_campsite_id_key UNIQUE (campsite_id);
    END IF;
END $$;

-- availability: needs a date to be useful; one row per (campsite, date).
ALTER TABLE availability ADD COLUMN IF NOT EXISTS date DATE;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'availability_campsite_date_key') THEN
        ALTER TABLE availability ADD CONSTRAINT availability_campsite_date_key UNIQUE (campsite_id, date);
    END IF;
END $$;

-- campsites: provenance / freshness columns.
ALTER TABLE campsites ADD COLUMN IF NOT EXISTS source            TEXT NOT NULL DEFAULT 'fs_usda';
ALTER TABLE campsites ADD COLUMN IF NOT EXISTS first_seen        TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE campsites ADD COLUMN IF NOT EXISTS last_scraped      TIMESTAMPTZ;
ALTER TABLE campsites ADD COLUMN IF NOT EXISTS primary_image_url TEXT;

-- Null out duplicate recreation_facility_id on higher-id rows so a partial
-- unique index can be created (one RIDB facility maps to one campsite row).
UPDATE campsites c
SET recreation_facility_id = NULL
WHERE recreation_facility_id IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM campsites c2
      WHERE c2.recreation_facility_id = c.recreation_facility_id
        AND c2.id < c.id
  );

CREATE UNIQUE INDEX IF NOT EXISTS campsites_recreation_facility_id_key
    ON campsites (recreation_facility_id)
    WHERE recreation_facility_id IS NOT NULL;
