-- 0022: provenance for status_updates.
--
-- status_updates.is_open has had exactly one writer (the fs.usda list tier),
-- so "where did this come from and how sure are we" was never a question the
-- table could answer. A second writer (recreation.gov availability) makes it
-- one: the two upsert on the same UNIQUE (campsite_id), so without provenance
-- a row is just a boolean of unknown origin, and a weaker source silently
-- overwriting a stronger one is invisible.
--
-- status_detail records the evidence in words, so a wrong flag on the site can
-- be traced back to the reasoning without re-running the job.

ALTER TABLE status_updates ADD COLUMN IF NOT EXISTS status_source TEXT;
ALTER TABLE status_updates ADD COLUMN IF NOT EXISTS status_detail TEXT;

-- Everything currently in the table came from the fs.usda list tier.
UPDATE status_updates
SET status_source = 'fs_usda'
WHERE status_source IS NULL AND is_open IS NOT NULL;

CREATE INDEX IF NOT EXISTS status_updates_source_idx ON status_updates (status_source);
