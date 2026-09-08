-- 0016_user_extras
-- Profile picture path + user-made collections of campsites.
--
--   users.avatar_path   - path under static/ to an uploaded image, or NULL
--   collections         - a named list a user creates ("Summer 2027",
--                          "Backpacking shortlist", ...)
--   collection_campsites - membership; a campsite can be in many collections

ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_path TEXT;

CREATE TABLE IF NOT EXISTS collections (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, name)
);

CREATE TABLE IF NOT EXISTS collection_campsites (
    collection_id INTEGER NOT NULL REFERENCES collections (id) ON DELETE CASCADE,
    campsite_id   INTEGER NOT NULL REFERENCES campsites (id) ON DELETE CASCADE,
    added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (collection_id, campsite_id)
);

CREATE INDEX IF NOT EXISTS collections_user_idx ON collections (user_id);
CREATE INDEX IF NOT EXISTS collection_campsites_campsite_idx
    ON collection_campsites (campsite_id);
