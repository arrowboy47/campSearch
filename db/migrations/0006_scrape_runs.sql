-- 0006_scrape_runs
-- Every scrape / refresh job writes one row here: when it ran, against which
-- source, how many rows it saw and upserted, how many errors. This is what
-- turns the pipeline from "run a script and hope" into something observable
-- (and cron-alertable).

CREATE TABLE IF NOT EXISTS scrape_runs (
    id            BIGSERIAL PRIMARY KEY,
    source        TEXT NOT NULL,          -- fs_usda | ridb | reserve_california | weather | status
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    rows_seen     INTEGER NOT NULL DEFAULT 0,
    rows_upserted INTEGER NOT NULL DEFAULT 0,
    errors        INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'running',  -- running | ok | failed
    note          TEXT
);

CREATE INDEX IF NOT EXISTS scrape_runs_source_started_idx
    ON scrape_runs (source, started_at DESC);
