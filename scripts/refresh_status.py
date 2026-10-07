#!/usr/bin/env python3
"""Open/closed status for reservable recreation.gov campgrounds.

Until now `status_updates` had one writer: the fs.usda list tier, which reads
the open/closed line off the forest page. That covers USFS scraped rows and
nothing else, so the `is_open` search facet was blind for the ~1400 campsites
that arrived via RIDB, ReserveCalifornia, The Dyrt or californiasbestcamping.

This job adds a second source for the subset where a real signal exists:
campgrounds with a `recreation_facility_id` that recreation.gov says are
reservable. It reads the public month-availability endpoint the site's own
booking calendar calls -- no key, no auth:

    GET recreation.gov/api/camps/availability/campground/<facility_id>/month
        ?start_date=YYYY-MM-01T00:00:00.000Z

and returns, per site, a status per day. The vocabulary seen in the wild is
`Available`, `Reserved`, `Closed`, `Not Reservable` and `NYR` (not yet
released).

**It does not guess.** The old `dynamic.py` wrote a hardcoded is_open and made
the facet worse than useless; the rule here returns None whenever the evidence
is ambiguous, and None means "we do not know", never "closed". In a 20-campground
sample this produced a definite verdict for 8 and abstained on 12 -- the
abstentions are the point.

    python scripts/refresh_status.py --dry-run --limit 20
    python scripts/refresh_status.py --limit 200
"""

import argparse
import collections
import contextlib
import datetime
import random
import sys
import time

import requests

from _pipeline import get_conn, scrape_run

API = "https://www.recreation.gov/api/camps/availability/campground/{fid}/month"
# The endpoint is the booking calendar's own; it rejects a default urllib/
# requests agent.
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# A day the public can actually take: the campground is operating.
OPEN_STATES = {"Available", "Reserved"}
# Explicitly shut for that day.
CLOSED_STATES = {"Closed"}
# Carries no information on its own. "Not Reservable" is both "this site is
# first-come" and "this campground is shut for the season"; "NYR" just means
# the booking window has not opened yet.
AMBIGUOUS_STATES = {"Not Reservable", "NYR"}

# How far forward to look. Long enough to see past a fully-booked week, short
# enough that next season's unreleased inventory does not drown the signal.
HORIZON_DAYS = 30
# Below this many site-days there is not enough evidence to call a closure.
MIN_EVIDENCE = 10

def classify(day_statuses, *, reservable, horizon_days=HORIZON_DAYS):
    """(is_open, detail) for one campground.

    `day_statuses` is an iterable of (date, status) pairs already restricted to
    the forward window. `reservable` is what recreation.gov says about the
    facility as a whole, and it is load-bearing: an all-"Not Reservable" window
    means "closed for the season" for a facility that takes bookings, and means
    nothing at all for one that never did.

    Returns is_open True / False / None, and a short human-readable reason that
    is stored alongside so a wrong flag can be traced without a re-run.
    """
    counts = collections.Counter(status for _, status in day_statuses)
    total = sum(counts.values())

    if not total:
        return None, "no availability data returned"

    open_days = sum(counts[s] for s in OPEN_STATES)
    closed_days = sum(counts[s] for s in CLOSED_STATES)

    # Someone can book it, or already has. Nothing outranks this.
    if open_days:
        return True, (
            f"{open_days} bookable site-days in the next {horizon_days}d "
            f"({_summarise(counts)})"
        )

    if closed_days:
        return False, (
            f"no bookable site-days and {closed_days} explicitly closed "
            f"in the next {horizon_days}d ({_summarise(counts)})"
        )

    # Everything left is ambiguous. For a facility that takes reservations, a
    # window with nothing bookable in it is a seasonal closure -- that is what
    # a mountain campground looks like in October. For a first-come facility it
    # is just the normal state of affairs.
    if reservable and total >= MIN_EVIDENCE and set(counts) <= AMBIGUOUS_STATES:
        if counts.get("Not Reservable"):
            return False, (
                f"reservable facility with nothing bookable for {horizon_days}d "
                f"({_summarise(counts)})"
            )
        return None, f"booking window not open yet ({_summarise(counts)})"

    return None, f"no usable signal ({_summarise(counts)})"


def _summarise(counts):
    return ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))


def month_starts(today, horizon_days=HORIZON_DAYS):
    """The first-of-month dates the window touches (the endpoint is monthly)."""
    end = today + datetime.timedelta(days=horizon_days)
    out, cur = [], today.replace(day=1)
    while cur <= end:
        out.append(cur)
        cur = (cur.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return out


def fetch_month(session, facility_id, month_start, timeout=30):
    resp = session.get(
        API.format(fid=facility_id),
        params={"start_date": f"{month_start.isoformat()}T00:00:00.000Z"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def window_statuses(payload, today, horizon_days=HORIZON_DAYS):
    """(date, status) pairs from one month payload, clipped to the window."""
    end = today + datetime.timedelta(days=horizon_days)
    for site in (payload.get("campsites") or {}).values():
        for day, status in (site.get("availabilities") or {}).items():
            try:
                dt = datetime.date.fromisoformat(day[:10])
            except ValueError:
                continue
            if today <= dt < end:
                yield dt, status


SELECT_SQL = """
    SELECT c.id, c.name, c.recreation_facility_id,
           COALESCE(r.is_reservable, FALSE) AS reservable
    FROM campsites c
    LEFT JOIN reservations r ON r.campsite_id = c.id
    WHERE c.recreation_facility_id IS NOT NULL
    ORDER BY c.id
"""

UPSERT_SQL = """
    INSERT INTO status_updates
        (campsite_id, is_open, last_checked, status_source, status_detail)
    VALUES (%(id)s, %(open)s, now(), 'recreation_gov', %(detail)s)
    ON CONFLICT (campsite_id)
    DO UPDATE SET
        is_open       = EXCLUDED.is_open,
        last_checked  = now(),
        status_source = EXCLUDED.status_source,
        status_detail = EXCLUDED.status_detail
"""


class _NoOpRunCounters:
    """Dummy counters for dry runs that writes nothing."""
    def __init__(self):
        self.seen = 0
        self.upserted = 0
        self.errors = 0
        self.note = None


@contextlib.contextmanager
def _no_op_scrape_run():
    """Context manager that yields counters but writes nothing.
    Used for --dry-run to ensure no scrape_runs row is created.
    """
    yield _NoOpRunCounters()


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true",
                   help="classify and print, write nothing")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--sleep", type=float, default=0.6,
                   help="base delay between facilities (jittered)")
    p.add_argument("--horizon", type=int, default=HORIZON_DAYS)
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    today = datetime.date.today()
    months = month_starts(today, args.horizon)

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json"})

    # Use no-op context manager for dry runs to avoid writing scrape_runs row
    run_context = _no_op_scrape_run() if args.dry_run else scrape_run("recreation_gov_status")

    with get_conn() as conn, run_context as run:
        sel = conn.cursor()
        sel.execute(SELECT_SQL + (f" LIMIT {int(args.limit)}" if args.limit else ""))
        rows = sel.fetchall()
        sel.close()

        upd = conn.cursor()
        verdicts = collections.Counter()

        for camp_id, name, facility_id, reservable in rows:
            run.seen += 1
            statuses = []
            failed = False
            for month in months:
                try:
                    payload = fetch_month(session, facility_id, month)
                except Exception as exc:  # noqa: BLE001
                    # One bad month should not throw away the other; a total
                    # failure is recorded as an error and the site is skipped.
                    print(f"  ! {name} ({facility_id}) {month}: "
                          f"{type(exc).__name__}: {exc}")
                    failed = True
                    continue
                statuses.extend(window_statuses(payload, today, args.horizon))
                time.sleep(args.sleep + random.uniform(0, args.sleep))

            if failed and not statuses:
                run.errors += 1
                continue

            is_open, detail = classify(
                statuses, reservable=reservable, horizon_days=args.horizon
            )
            verdicts["open" if is_open else "closed" if is_open is False else "unknown"] += 1

            if is_open is None:
                # Never overwrite a known status with a shrug -- the fs.usda
                # tier may already have a real answer for this row.
                if args.dry_run:
                    print(f"  ? {name}: {detail}")
                continue

            if args.dry_run:
                print(f"  {'OPEN  ' if is_open else 'CLOSED'} {name}: {detail}")
                continue

            upd.execute(UPSERT_SQL,
                        {"id": camp_id, "open": is_open, "detail": detail[:500]})
            run.upserted += 1

        if not args.dry_run:
            conn.commit()
        upd.close()
        run.note = ", ".join(f"{k}={v}" for k, v in sorted(verdicts.items()))
        print(f"[status] {run.note}")


if __name__ == "__main__":
    main()
