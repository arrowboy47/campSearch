-- 0017_pick_count
-- Selection counter: how often a campsite is opened from a results list.
--
--   campsites.pick_count     - running total of result-list click-throughs
--   campsites.last_picked_at  - when the most recent one happened
--
-- The search scorer adds a small, log-scaled, capped bonus from pick_count so a
-- campsite people actually choose floats up among otherwise-equal name matches,
-- without ever letting popularity beat a real substring hit.

ALTER TABLE campsites ADD COLUMN IF NOT EXISTS pick_count    INTEGER NOT NULL DEFAULT 0;
ALTER TABLE campsites ADD COLUMN IF NOT EXISTS last_picked_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS campsites_pick_count_idx ON campsites (pick_count DESC);
