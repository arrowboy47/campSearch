-- 0007_agencies_ridb
-- Room to map our agency rows to RIDB OrgIDs so the RIDB sync can enumerate
-- facilities per organization (National Park Service, BLM, USFS, ...).
-- The actual IDs are looked up once from GET /organizations in Phase 5 and
-- written by scripts/sync_ridb.py --seed-agencies; left NULL here on purpose
-- rather than hardcoding unverified numbers.

ALTER TABLE agencies ADD COLUMN IF NOT EXISTS ridb_org_id INTEGER;

CREATE UNIQUE INDEX IF NOT EXISTS agencies_ridb_org_id_key
    ON agencies (ridb_org_id)
    WHERE ridb_org_id IS NOT NULL;
