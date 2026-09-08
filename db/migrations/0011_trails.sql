-- 0011_trails
-- Nearby-trails data from AllTrails (via the claude.ai AllTrails MCP connector).
-- `trails` is keyed on the AllTrails trail id directly (globally unique, stable).
-- `campsite_trails` links a campsite to a trail with the trailhead distance
-- reported by the "find trails near location" call (approximate: measured from
-- the campsite's coords, or a rounded grid point standing in for a cluster of
-- nearby campsites).

CREATE TABLE IF NOT EXISTS trails (
    id                  BIGINT PRIMARY KEY,           -- AllTrails trail id
    name                TEXT NOT NULL,
    slug                TEXT,
    url                 TEXT,
    difficulty          TEXT,                         -- Easy | Moderate | Hard
    route_type          TEXT,                         -- Loop | Out & back | Point to point
    length_miles        NUMERIC,
    elevation_gain_feet NUMERIC,
    elevation_max_feet  NUMERIC,
    avg_rating          NUMERIC,
    reviews_count       INTEGER,
    activities          TEXT[] DEFAULT '{}',
    features            TEXT[] DEFAULT '{}',
    location_label      TEXT,
    description         TEXT,                         -- only filled for detail-tier fetches
    review_summary      TEXT,
    photo_url           TEXT,
    first_seen          TIMESTAMPTZ DEFAULT now(),
    last_scraped        TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS campsite_trails (
    campsite_id  INTEGER NOT NULL REFERENCES campsites (id) ON DELETE CASCADE,
    trail_id     BIGINT  NOT NULL REFERENCES trails (id) ON DELETE CASCADE,
    distance_miles NUMERIC,
    last_seen    TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (campsite_id, trail_id)
);

CREATE INDEX IF NOT EXISTS campsite_trails_trail_id_idx ON campsite_trails (trail_id);
