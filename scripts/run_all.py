#!/usr/bin/env python3
"""Orchestrator for the refresh jobs. Point the systemd timers at this.

    python scripts/run_all.py daily     # weather for the next 2 days, call-budgeted
    python scripts/run_all.py weekly    # static re-scrape + RIDB (matches + org ingest)
    python scripts/run_all.py smoke     # cheap end-to-end check

Each underlying job writes its own scrape_runs row; this sequences them and exits
non-zero if any job raised, so the OnFailure handler / alerting has a signal.

Sizing note: a full weather sweep is ~1 API call per (site, day) at ~1s each —
1000+ sites makes 7-day sweeps multi-hour. So the daily job is capped with
--max-calls and only looks 2 days out; the weekly job does NOT do a big weather
pass (the daily one keeps it fresh).
"""

import sys

import refresh_dynamic
import sync_ridb
import scrape_fs_usda
import ingest_ridb_orgs
import scrape_reservecalifornia
import scrape_thedyrt
import clean_text
import backfill_elevation
import derive_attributes


JOBS = {
    "daily": [
        # today + tomorrow, ~900 calls at ~1.3s each (~20 min); leftovers roll over.
        lambda: refresh_dynamic.main(["--days", "2", "--max-calls", "900", "--sleep", "0.3"]),
    ],
    "weekly": [
        lambda: scrape_fs_usda.main(["--all"]),              # static + open/closed status
        lambda: scrape_fs_usda.main(["--all", "--detail"]),  # backfill coords/fee for new sites
        lambda: sync_ridb.main([]),                          # match sites missing a facility id
        lambda: ingest_ridb_orgs.main(["--org", "all", "--state", "CA"]),  # NPS/BLM/USFS campgrounds
        lambda: scrape_reservecalifornia.main([]),           # CA state park campgrounds
        lambda: scrape_thedyrt.main([]),                     # dispersed / free camping (The Dyrt)
        lambda: backfill_elevation.main([]),                 # elevation_ft + terrain for new coords
        lambda: derive_attributes.main([]),                  # activities[] / water_feature / toilet_type
        lambda: clean_text.main([]),                         # normalize fee / overview / seasons text
        lambda: refresh_dynamic.main(["--days", "3", "--max-calls", "1200", "--sleep", "0.3"]),
    ],
    "smoke": [
        lambda: refresh_dynamic.main(["--limit", "3", "--sleep", "0"]),
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
