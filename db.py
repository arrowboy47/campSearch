import psycopg2

from config import database_url


def get_connection():
    """Open a new Postgres connection from DATABASE_URL.

    Was hardcoded to a local unix socket (dbname=camping user=arrowboy); now
    points wherever DATABASE_URL says — a local dev container, or campsearch-pg
    on thebigbox over an SSH tunnel.
    """
    return psycopg2.connect(database_url())


# useful for weather data and anything that needs the campsite site_url: so things like updating site status and when it was last updated
# returns dict with id, name, latitude, longitude, site_url
def get_campsite_by_id(campsite_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, latitude, longitude, site_url
        FROM campsites
        WHERE id = %s;
    """, (campsite_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if row:
        return {
            "id": row[0],
            "name": row[1],
            "latitude": row[2],
            "longitude": row[3],
            "site_url": row[4],
        }
    else:
        return None
