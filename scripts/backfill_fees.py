#!/usr/bin/env python3
"""Backfill is_free and fee_min for campsites where they are NULL.

Many campsites (mostly from reserve_california and ridb) have NULL is_free
and fee_min even though the source text contains fee information. This script
parses that text to derive the values.

The parser is conservative: it only writes a value if the source text clearly
states a camping fee. A missing price is not evidence of free camping.

    python scripts/backfill_fees.py            # dry run, shows report
    python scripts/backfill_fees.py --commit   # writes changes + scrape_run entry
    python scripts/backfill_fees.py --limit 50
    python scripts/backfill_fees.py --source ridb

The query looks for: is_free IS NULL OR fee_min IS NULL. When parsing, the
script tries fee_raw first (the raw scraped text), then fee, then overview.
If the parser finds a value, it fills both is_free and fee_min/fee_max.

Idempotent: running twice with the same input produces no changes on the second run.
"""

import argparse
import re
from collections import Counter

from _pipeline import get_conn, scrape_run
from clean_text import parse_fee

# Find which text field has content worth parsing
SELECT_CANDIDATES = """
    SELECT id, name, source, fee_raw, fee, overview
    FROM campsites
    WHERE is_free IS NULL OR fee_min IS NULL
    {source_filter}
    ORDER BY source, id
"""

UPDATE_SQL = """
    UPDATE campsites
    SET is_free = %(is_free)s, fee_min = %(fee_min)s, fee_max = %(fee_max)s
    WHERE id = %(id)s
"""


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", default=True,
                   help="Show report without writing (default)")
    p.add_argument("--commit", action="store_false", dest="dry_run",
                   help="Write changes + scrape_run entry")
    p.add_argument("--limit", type=int, default=None,
                   help="Process at most N rows (for testing)")
    p.add_argument("--source", type=str, default=None,
                   help="Restrict to one source (e.g. 'ridb')")
    return p.parse_args(argv)


def try_parse_fee(fee_raw, fee, overview):
    """Try to parse fee from one of the text fields.

    Returns (display, fee_min, fee_max, is_free) or (None, None, None, None).
    """
    # Try fee_raw first (raw scraped text, most likely to have structured fee info)
    if fee_raw:
        display, fmin, fmax, is_free = parse_fee(fee_raw)
        if display is not None:
            return display, fmin, fmax, is_free

    # Fall back to fee (sometimes already processed)
    if fee:
        display, fmin, fmax, is_free = parse_fee(fee)
        if display is not None:
            return display, fmin, fmax, is_free

    # Try overview (often contains prose, rarely structured fees)
    if overview:
        # Only try parsing overview if it explicitly mentions a campground fee.
        # Avoid noise like "$20 no-show fee" or "$10 service fee".
        # Look for patterns like "X per night" or "campground fee"
        if re.search(r'\$.*(?:per\s+night|camping|campground|nightly)', overview, re.I):
            display, fmin, fmax, is_free = parse_fee(overview)
            if display is not None:
                return display, fmin, fmax, is_free

    return None, None, None, None


def main(argv=None):
    args = parse_args(argv)

    source_filter = ""
    query_args = ()
    if args.source:
        source_filter = "AND source = %s"
        query_args = (args.source,)

    query = SELECT_CANDIDATES.format(source_filter=source_filter)
    if args.limit:
        query += f" LIMIT {int(args.limit)}"

    with get_conn() as conn:
        # For scrape_run when committing
        run = scrape_run("backfill_fees") if not args.dry_run else None

        cur = conn.cursor()
        cur.execute(query, query_args)
        rows = cur.fetchall()
        cur.close()

        resolved_by_source = Counter()
        unresolved_by_source = Counter()
        resolved_rows = []
        unresolved_texts = []

        for cid, name, source, fee_raw, fee, overview in rows:
            display, fmin, fmax, is_free = try_parse_fee(fee_raw, fee, overview)

            if display is not None:
                resolved_by_source[source] += 1
                resolved_rows.append((cid, name, source, display, is_free, fmin))

                if not args.dry_run:
                    upd = conn.cursor()
                    upd.execute(UPDATE_SQL, {
                        "id": cid,
                        "is_free": is_free,
                        "fee_min": fmin,
                        "fee_max": fmax,
                    })
                    if run:
                        run.upserted += 1
                    upd.close()
            else:
                unresolved_by_source[source] += 1
                # Collect unparseable text for pattern analysis
                for text in [fee_raw, fee, overview]:
                    if text and text.strip():
                        unresolved_texts.append(text)

            if run:
                run.seen += 1

        if not args.dry_run:
            conn.commit()

    # Print report
    total_candidates = len(rows)
    total_resolved = sum(resolved_by_source.values())
    total_unresolved = sum(unresolved_by_source.values())

    print(f"\nBackfill Fees Report")
    print("=" * 60)
    print(f"Total candidates with NULL is_free/fee_min: {total_candidates}")
    print(f"Resolved: {total_resolved}")
    print(f"Unresolved: {total_unresolved}")
    if args.dry_run:
        print("(dry run, not written)")

    print(f"\nBy source:")
    all_sources = sorted(set(resolved_by_source.keys()) | set(unresolved_by_source.keys()))
    for source in all_sources:
        res = resolved_by_source.get(source, 0)
        unres = unresolved_by_source.get(source, 0)
        print(f"  {source:30} resolved={res:3}  unresolved={unres:3}")

    # Show example resolved rows
    if resolved_rows:
        print(f"\nExample resolved rows (first 10):")
        for cid, name, source, display, is_free, fmin in resolved_rows[:10]:
            free_str = "free" if is_free else f"${fmin}"
            print(f"  [{cid:4}] {name[:40]:40} ({source:20}) -> {display}")

    # Find common unparseable patterns
    if unresolved_texts:
        print(f"\nMost common unparseable text patterns (10 most frequent):")
        # Summarize patterns: first 80 chars or first line
        patterns = []
        for text in unresolved_texts:
            # First line only, up to 80 chars
            first_line = text.split('\n')[0][:80].strip()
            if first_line:
                patterns.append(first_line)
        pattern_counts = Counter(patterns)
        for pattern, count in pattern_counts.most_common(10):
            print(f"  [{count:3}] {pattern}")

    print()


if __name__ == "__main__":
    main()
