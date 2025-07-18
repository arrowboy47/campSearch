-- CAMPSITES (core static info)
CREATE TABLE campsites (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    latitude DECIMAL(9,6),
    longitude DECIMAL(9,6),
    address TEXT,
    managing_unit TEXT,  -- e.g., "Angeles National Forest"
    reservation_type TEXT,  -- e.g., "First-come", "Reservation-only"
    fs_usda_url TEXT,
    reservation_url TEXT,
    contact_name TEXT,
    contact_phone TEXT,
    seasons_of_use TEXT,
    num_sites INTEGER,
    fee TEXT
);

-- AMENITIES (you can expand this if you want more fields later)
CREATE TABLE amenities (
    id SERIAL PRIMARY KEY,
    campsite_id INTEGER REFERENCES campsites(id) ON DELETE CASCADE,
    water_activities BOOLEAN,
    boating BOOLEAN
);

-- IMAGES (multiple per campsite)
CREATE TABLE images (
    id SERIAL PRIMARY KEY,
    campsite_id INTEGER REFERENCES campsites(id) ON DELETE CASCADE,
    image_url TEXT NOT NULL,
    description TEXT
);

-- STATUS UPDATES (open/closed and fire restrictions)
CREATE TABLE status_updates (
    id SERIAL PRIMARY KEY,
    campsite_id INTEGER REFERENCES campsites(id) ON DELETE CASCADE,
    is_open BOOLEAN,
    fire_restrictions TEXT,
    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- WEATHER FORECASTS (store as JSON or structured)
CREATE TABLE weather_forecasts (
    id SERIAL PRIMARY KEY,
    campsite_id INTEGER REFERENCES campsites(id) ON DELETE CASCADE,
    forecast_json JSONB,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- AVAILABILITY (optional and experimental)
CREATE TABLE availability (
    id SERIAL PRIMARY KEY,
    campsite_id INTEGER REFERENCES campsites(id) ON DELETE CASCADE,
    is_available BOOLEAN,
    availability_raw TEXT,  -- you can store calendar data or notes here
    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

