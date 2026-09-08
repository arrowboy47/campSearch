#!/usr/bin/env python3
"""Apply db/migrations/*.sql in filename order, once each.

Applied files are recorded in schema_migrations, so re-running is a no-op.
Each file runs in its own transaction: a failure rolls that file back and stops
(earlier files stay applied).

Usage:
    DATABASE_URL=postgresql://user:pw@host:port/db  python scripts/migrate.py
    python scripts/migrate.py --status      # list applied / pending, apply nothing
"""

import os
import sys
import pathlib

import psycopg2
from dotenv import load_dotenv

load_dotenv()

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "db" / "migrations"


def connect():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("DATABASE_URL not set (copy .env.example to .env)")
    return psycopg2.connect(dsn)


def ensure_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )


def main():
    status_only = "--status" in sys.argv

    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        sys.exit(f"no .sql files in {MIGRATIONS_DIR}")

    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()

    ensure_table(cur)
    conn.commit()

    cur.execute("SELECT version FROM schema_migrations;")
    done = {row[0] for row in cur.fetchall()}

    if status_only:
        for path in files:
            mark = "applied" if path.stem in done else "PENDING"
            print(f"{mark:8} {path.stem}")
        return

    applied = 0
    for path in files:
        version = path.stem
        if version in done:
            print(f"skip  {version}")
            continue

        print(f"apply {version} ...", end=" ", flush=True)
        try:
            cur.execute(path.read_text())
            cur.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s);", (version,)
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - want the message, then bail
            conn.rollback()
            print("FAILED")
            print(exc)
            sys.exit(1)
        print("ok")
        applied += 1

    print(f"\n{applied} applied, {len(done)} already present, {len(files)} total.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
