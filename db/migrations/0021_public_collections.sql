-- 0021_public_collections
-- Opt-in sharing: a user can mark a collection public so it shows up on the
-- /users directory and their /users/<username> page. Everything else about a
-- user (email, home address, saved list) stays private.

ALTER TABLE collections ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS collections_public_idx ON collections (is_public) WHERE is_public;
