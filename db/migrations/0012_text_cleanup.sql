-- 0012_text_cleanup
-- The scraped `fee` text is a mess: fs.usda stores multi-paragraph pass-discount
-- policy prose (avg 140 chars, up to 1852) where the actual price is only the
-- first line or two. The Dyrt fees are already clean ("$5" / "Free").
--
-- This migration only adds the columns the normalizer needs; the parsing itself
-- lives in scripts/clean_text.py (too fiddly for SQL, and it must stay rerunnable
-- from the untouched raw text).
--
--   fee_raw  - the original scraped string, kept verbatim so the parser can be
--              re-run / improved later without re-scraping
--   fee      - short canonical display string ("Single $25 · Double $50 / night")
--   fee_min  - lowest nightly site price in dollars (for sort / range filters)
--   fee_max  - highest nightly site price
--   is_free  - true when the listing explicitly says no fee

ALTER TABLE campsites ADD COLUMN IF NOT EXISTS fee_raw TEXT;
ALTER TABLE campsites ADD COLUMN IF NOT EXISTS fee_min NUMERIC(8, 2);
ALTER TABLE campsites ADD COLUMN IF NOT EXISTS fee_max NUMERIC(8, 2);
ALTER TABLE campsites ADD COLUMN IF NOT EXISTS is_free BOOLEAN;

-- Snapshot the current fee into fee_raw once. Guard on fee_raw IS NULL so a
-- re-run (or a run after clean_text.py has rewritten `fee`) never clobbers the
-- preserved original.
UPDATE campsites
SET fee_raw = fee
WHERE fee_raw IS NULL
  AND fee IS NOT NULL;

CREATE INDEX IF NOT EXISTS campsites_fee_min_idx ON campsites (fee_min);
