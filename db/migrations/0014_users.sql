-- 0014_users
-- Optional user accounts. You never need one to use the app; signing up just
-- unlocks saving campsites and a "near me" distance that falls back to a saved
-- home location when the browser won't share device location.
--
-- home_address is free text (a city, or a full street address) entered by the
-- user; home_lat / home_lon are geocoded from it once at save time so distance
-- math doesn't re-geocode on every page view.

CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    first_name    TEXT,
    last_name     TEXT,
    email         TEXT,
    home_address  TEXT,
    home_lat      NUMERIC,
    home_lon      NUMERIC,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS saved_campsites (
    user_id     INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    campsite_id INTEGER NOT NULL REFERENCES campsites (id) ON DELETE CASCADE,
    saved_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, campsite_id)
);

CREATE INDEX IF NOT EXISTS saved_campsites_user_idx ON saved_campsites (user_id);
