from db import get_connection


conn = get_connection()
cur = conn.cursor()

# Add the column 
cur.execute("ALTER TABLE campsites ADD COLUMN IF NOT EXISTS forest_name TEXT;")

# select all ids, and fs_usda_name
cur.execute("SELECT id, fs_usda_url FROM campsites;")
all_sites = cur.fetchall()

# extrcat the fs_usda_url from the fs_usda_name
forest_names = []
site_ids = []
for site_id, url in all_sites:
    forest_name = url.rstrip("/").split("/")[-1]
    forest_names.append(forest_name)
    site_ids.append(site_id)

# insert the forest name into the database
cur.executemany("""
    UPDATE campsites
    SET forest_name = %s
    WHERE id = %s;
""", zip(forest_names, site_ids))

# Commit changes and clean up
conn.commit()
cur.close()
conn.close()