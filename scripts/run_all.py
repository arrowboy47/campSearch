#!/usr/bin/env python3
"""Orchestrator for the refresh jobs. Point cron at this.

    python scripts/run_all.py daily     # weather refresh (today), + status when it exists
    python scripts/run_all.py weekly    # daily + RIDB reservation re-sync

Each underlying job writes its own scrape_runs row; this just sequences them and
exits non-zero if any job raised, so cron / Telegram alerting has something to
key on.
"""

import sys

import refresh_dynamic
import sync_ridb


JOBS = {
    "daily": [
        lambda: refresh_dynamic.main(["--days", "1"]),
    ],
    "weekly": [
        lambda: refresh_dynamic.main(["--days", "7"]),
        lambda: sync_ridb.main([]),  # sites still missing a facility id
    ],
}


def main(argv=None):
    argv = argv or sys.argv[1:]
    mode = argv[0] if argv else "daily"
    if mode not in JOBS:
        sys.exit(f"usage: run_all.py [{' | '.join(JOBS)}]")

    failures = 0
    for job in JOBS[mode]:
        try:
            job()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"job failed: {type(exc).__name__}: {exc}")

    if failures:
        sys.exit(f"{failures} job(s) failed")


if __name__ == "__main__":
    main()
