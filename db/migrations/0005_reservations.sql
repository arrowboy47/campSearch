-- 0005_reservations
-- Dedicated table for recreation.gov / ReserveCalifornia reservation facts,
-- from the "campsite page" vault note. Kept separate from campsites so a
-- reservation refresh can run on its own cadence.

CREATE TABLE IF NOT EXISTS reservations (
    id               SERIAL PRIMARY KEY,
    campsite_id      INTEGER NOT NULL REFERENCES campsites(id) ON DELETE CASCADE,
    facility_id      TEXT,                       -- RIDB FacilityID or provider key
    provider         TEXT NOT NULL DEFAULT 'recreation_gov',  -- recreation_gov | reserve_california
    reservation_type TEXT,                       -- first-come, reservation-only, dispersed
    reservation_url  TEXT,
    is_reservable    BOOLEAN,
    num_sites        INTEGER,
    last_checked     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT reservations_campsite_id_key UNIQUE (campsite_id)
);

CREATE INDEX IF NOT EXISTS reservations_facility_id_idx ON reservations (facility_id);
