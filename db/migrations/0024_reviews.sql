-- 0024_reviews
-- User-submitted reviews and photos for campsites. Reviews are account-only,
-- one per user per campsite, gated on an honor-system claim of having visited.
--
--   reviews.verdict        - boolean: true = thumbs up, false = thumbs down.
--   reviews.status         - 'published' or 'removed'. Text publishes immediately,
--                            unlike photos which wait for approval.
--   reviews.visited        - honor-system claim. We do not verify actual visits.
--   review_photos.status   - 'pending', 'approved', or 'rejected'. Photos require
--                            manual approval before they appear.
--   review_attribute_reports - user reports that a scraped attribute is wrong.
--                              Reports accumulate; they are applied by a gated job,
--                              not by direct overwrites, because weekly scrapers
--                              would clobber direct edits anyway.
--
-- Foreign keys all CASCADE on delete, unlike user_events (SET NULL). A review is
-- content authored by a user about a campsite, so deleting either should remove it.
-- Behavioral history (user_events) is different: it records what happened and must
-- outlive both user and campsite.
--
-- The CHECK constraints are declared inline to ensure idempotency. Postgres has no
-- ADD CONSTRAINT IF NOT EXISTS, so a separate ALTER would fail on a second run.

CREATE TABLE IF NOT EXISTS reviews (
    id          BIGSERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    campsite_id INTEGER NOT NULL REFERENCES campsites (id) ON DELETE CASCADE,
    verdict     BOOLEAN NOT NULL,
    body        TEXT,
    visited     BOOLEAN NOT NULL DEFAULT true,
    status      TEXT NOT NULL DEFAULT 'published',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, campsite_id),
    CONSTRAINT reviews_status_check CHECK (status IN ('published', 'removed'))
);

CREATE TABLE IF NOT EXISTS review_photos (
    id         BIGSERIAL PRIMARY KEY,
    review_id  BIGINT NOT NULL REFERENCES reviews (id) ON DELETE CASCADE,
    path       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT review_photos_status_check CHECK (status IN ('pending', 'approved', 'rejected'))
);

CREATE TABLE IF NOT EXISTS review_attribute_reports (
    id            BIGSERIAL PRIMARY KEY,
    review_id     BIGINT NOT NULL REFERENCES reviews (id) ON DELETE CASCADE,
    campsite_id   INTEGER NOT NULL REFERENCES campsites (id) ON DELETE CASCADE,
    attribute     TEXT NOT NULL,
    claimed_value TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT review_attribute_reports_attribute_check CHECK (
        attribute IN (
            'water',
            'toilet_type',
            'is_free',
            'fee_min',
            'is_reservable',
            'is_open'
        )
    )
);

CREATE INDEX IF NOT EXISTS reviews_campsite_status_idx ON reviews (campsite_id, status);
CREATE INDEX IF NOT EXISTS reviews_user_idx ON reviews (user_id);
CREATE INDEX IF NOT EXISTS review_photos_review_status_idx ON review_photos (review_id, status);
CREATE INDEX IF NOT EXISTS review_attribute_reports_campsite_idx ON review_attribute_reports (campsite_id, attribute);
