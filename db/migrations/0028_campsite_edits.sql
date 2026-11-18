-- 0028_campsite_edits
-- Audit trail for hand edits an admin makes to campsite metadata from the
-- /eval labelling page. One row per changed field, so a bad edit can be seen
-- and reverted from old_value.
--
-- NOTE: the scrapers and derive_attributes.py rewrite these columns on each
-- refresh. This table is the record of what was hand-corrected, so those edits
-- can be re-applied or protected later; it does not stop a refresh from
-- overwriting them.

CREATE TABLE IF NOT EXISTS campsite_edits (
    id          BIGSERIAL PRIMARY KEY,
    campsite_id INTEGER NOT NULL REFERENCES campsites (id) ON DELETE CASCADE,
    admin_id    INTEGER NULL REFERENCES users (id) ON DELETE SET NULL,
    field       TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS campsite_edits_campsite_idx
    ON campsite_edits (campsite_id, created_at DESC);
