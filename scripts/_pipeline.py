"""Shared plumbing for the scrape / refresh jobs.

Every job wraps its work in ``scrape_run(source)``: that inserts a row in
``scrape_runs`` when it starts, and on exit stamps ``finished_at``, the row
counts, and ``status`` (ok / failed). So "did the nightly refresh work, and how
much did it touch" is one SQL query, not a guess.

    from _pipeline import get_conn, scrape_run

    with get_conn() as conn, scrape_run("weather") as run:
        ...
        run.seen += 1
        run.upserted += 1

``scrape_run`` uses its own dedicated connection for the bookkeeping row, so it
is independent of the caller's transaction state (commits, rollbacks, autocommit
all fine on the caller side).
"""

import os
import sys
import contextlib

import psycopg2

# Allow `python scripts/foo.py` to import config.py from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import database_url  # noqa: E402


def get_conn():
    return psycopg2.connect(database_url())


class RunCounters:
    __slots__ = ("seen", "upserted", "errors", "note")

    def __init__(self):
        self.seen = 0
        self.upserted = 0
        self.errors = 0
        self.note = None


@contextlib.contextmanager
def scrape_run(source):
    rec_conn = get_conn()
    rec_conn.autocommit = True
    rec = rec_conn.cursor()
    rec.execute(
        "INSERT INTO scrape_runs (source, status) VALUES (%s, 'running') RETURNING id;",
        (source,),
    )
    run_id = rec.fetchone()[0]

    counters = RunCounters()
    status = "ok"
    try:
        yield counters
    except BaseException as exc:  # noqa: BLE001 - record then re-raise
        status = "failed"
        counters.errors += 1
        counters.note = f"{type(exc).__name__}: {exc}"[:500]
        raise
    finally:
        # A run that only produced errors is a failure even if no exception
        # bubbled out — cron alerting keys off status.
        if status == "ok" and counters.errors and not counters.upserted:
            status = "failed"
        rec.execute(
            """
            UPDATE scrape_runs
            SET finished_at = now(), rows_seen = %s, rows_upserted = %s,
                errors = %s, status = %s, note = %s
            WHERE id = %s;
            """,
            (counters.seen, counters.upserted, counters.errors, status,
             counters.note, run_id),
        )
        rec.close()
        rec_conn.close()
        print(
            f"[{source}] run {run_id}: {status} "
            f"seen={counters.seen} upserted={counters.upserted} errors={counters.errors}"
        )
