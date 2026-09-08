-- 0004_weather_per_day
-- weather_forecasts was UNIQUE(campsite_id) = one cached forecast per site.
-- The refresh job needs per-day rows so a date range can be filled in and
-- re-checked day by day. Move the unique key to (campsite_id, forecast_date).

ALTER TABLE weather_forecasts ADD COLUMN IF NOT EXISTS forecast_date DATE;

UPDATE weather_forecasts
SET forecast_date = (forecast_json ->> 'date')::date
WHERE forecast_date IS NULL
  AND forecast_json ? 'date'
  AND (forecast_json ->> 'date') ~ '^\d{4}-\d{2}-\d{2}$';

-- Fallback for any row whose JSON had no usable date.
UPDATE weather_forecasts
SET forecast_date = last_updated::date
WHERE forecast_date IS NULL;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'weather_forecasts_campsite_id_key') THEN
        ALTER TABLE weather_forecasts DROP CONSTRAINT weather_forecasts_campsite_id_key;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'weather_forecasts_campsite_date_key') THEN
        ALTER TABLE weather_forecasts
            ADD CONSTRAINT weather_forecasts_campsite_date_key UNIQUE (campsite_id, forecast_date);
    END IF;
END $$;
