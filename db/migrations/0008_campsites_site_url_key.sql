-- 0008_campsites_site_url_key
-- site_url (the fs.usda.gov detail-page URL) is unique across all 900 rows and
-- is the natural key the fs_usda scraper matches on for its upserts.

CREATE UNIQUE INDEX IF NOT EXISTS campsites_site_url_key
    ON campsites (site_url)
    WHERE site_url IS NOT NULL;
