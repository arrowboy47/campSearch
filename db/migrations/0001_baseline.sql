-- 0001_baseline
-- Marks the schema restored from the 2026-09 pg_dump of the laptop `camping` DB
-- as the starting point. No DDL: the dump already created campsites, amenities,
-- images, status_updates, weather_forecasts, availability, agencies.
-- Later migrations (0002+) bring that schema up to the revamp v2 shape.

DO $$
BEGIN
    IF to_regclass('public.campsites') IS NULL THEN
        RAISE EXCEPTION '0001_baseline: expected table campsites to exist (restore the dump first)';
    END IF;
END $$;
