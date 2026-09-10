#!/usr/bin/env python3
"""Set a user's password from the command line.

Lockout recovery for a small deployment: there is no self-service email reset
yet (see the roadmap), so a locked-out user is fixed by the operator running
this against the database.

    python scripts/set_password.py <username>
    python scripts/set_password.py <username> --password 's3cr3t'   # non-interactive
    python scripts/set_password.py --list                           # show usernames

Without --password the new password is read twice from a no-echo prompt.
The stored value is a werkzeug PBKDF2 hash, identical to what signup writes.
"""

import argparse
import getpass
import sys

from _pipeline import get_conn
from psycopg2.extras import RealDictCursor
from werkzeug.security import generate_password_hash

MIN_LEN = 8


def list_users(cur):
    cur.execute("SELECT username, email FROM users ORDER BY username;")
    rows = cur.fetchall()
    if not rows:
        print("(no users)")
        return
    for r in rows:
        print(f"  {r['username']:<24} {r['email'] or ''}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Set a user's password.")
    ap.add_argument("username", nargs="?", help="username to update")
    ap.add_argument("--password", help="new password (skips the prompt)")
    ap.add_argument("--list", action="store_true", help="list usernames and exit")
    args = ap.parse_args(argv)

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        if args.list:
            list_users(cur)
            return 0

        if not args.username:
            ap.error("username is required (or pass --list)")

        cur.execute(
            "SELECT id, username FROM users WHERE lower(username) = lower(%s);",
            (args.username,),
        )
        row = cur.fetchone()
        if not row:
            print(f"No user named {args.username!r}.", file=sys.stderr)
            print("Known usernames:", file=sys.stderr)
            list_users(cur)
            return 1

        if args.password is not None:
            new_pw = args.password
        else:
            new_pw = getpass.getpass(f"New password for {row['username']}: ")
            if new_pw != getpass.getpass("Confirm: "):
                print("Passwords did not match.", file=sys.stderr)
                return 1

        if len(new_pw) < MIN_LEN:
            print(f"Password must be at least {MIN_LEN} characters.", file=sys.stderr)
            return 1

        cur.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s;",
            (generate_password_hash(new_pw), row["id"]),
        )
        conn.commit()
        print(f"Password updated for {row['username']} (id {row['id']}).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
