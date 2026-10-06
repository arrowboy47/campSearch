-- 0023_user_events
-- Append-only event log for user behavior. This is the source of truth for
-- interactions (searches, clicks, saves, reviews). Derived metrics like affinity
-- scores and popularity are recomputed from this log, never written in place, so
-- that re-tuning a weighting is a recompute not a data loss.
--
--   user_id / anon_id  - exactly one is normally set; null for the other. A user
--                        signup merges anon rows to user_id without fighting a
--                        CHECK constraint, so we do not add one.
--   position           - 1-based rank in the result list for impression and click
--                        events; stored raw so position-bias decay can be re-tuned
--                        at derivation time without destroying the underlying data.
--   result_ids         - array of campsite IDs returned for a search, in order.
--   event_type         - one of the nine allowed types, enforced by CHECK.
--   filters / meta     - jsonb for extensibility; new keys can be added without
--                        schema migrations.
--
-- The event_type CHECK is declared inline rather than added afterwards.
-- Postgres has no ADD CONSTRAINT IF NOT EXISTS, so a separate ALTER would make
-- this migration fail on a second run; inline, it is covered by the table's own
-- IF NOT EXISTS guard and the whole file stays re-runnable.

CREATE TABLE IF NOT EXISTS user_events (
    id            BIGSERIAL PRIMARY KEY,
    user_id       INTEGER NULL REFERENCES users (id) ON DELETE SET NULL,
    anon_id       UUID NULL,
    session_id    UUID NULL,
    event_type    TEXT NOT NULL,
    campsite_id   INTEGER NULL REFERENCES campsites (id) ON DELETE SET NULL,
    position      INTEGER NULL,
    query_text    TEXT NULL,
    filters       JSONB NULL,
    result_ids    INTEGER[] NULL,
    meta          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT user_events_event_type_check CHECK (
        event_type IN (
            'search_performed',
            'result_impression',
            'result_clicked',
            'campsite_viewed',
            'saved',
            'unsaved',
            'collection_added',
            'review_submitted',
            'search_feedback'
        )
    )
);

CREATE INDEX IF NOT EXISTS user_events_user_created_idx ON user_events (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS user_events_anon_created_idx ON user_events (anon_id, created_at DESC);
CREATE INDEX IF NOT EXISTS user_events_campsite_type_idx ON user_events (campsite_id, event_type);
CREATE INDEX IF NOT EXISTS user_events_type_created_idx ON user_events (event_type, created_at DESC);

