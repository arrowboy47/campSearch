#!/usr/bin/env python3
"""Validate docs/search/golden_set.yaml against the live corpus.

Checks structure and answerability, not search quality: every slug unique,
every known-item id present in the database, every predicate valid SQL that
matches enough campsites to make precision@k meaningful, every vibe entry's
ids real. It never runs a search, so it is fast and cannot be gamed by the
ranking it is meant to judge.

Run it after editing the golden set, and in CI once there is one.

    python scripts/validate_golden_set.py
    python scripts/validate_golden_set.py --file other.yaml

Exits 0 when the set is usable, 1 when it is not.
"""

import argparse
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_connection

DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs", "search", "golden_set.yaml",
)

# A predicate matching fewer than this makes precision@k unreachable: with 10
# matches in the whole corpus, precision@10 of 1.0 demands a perfect ranking
# and any miss looks like a catastrophe. Flagged, not fatal.
MIN_PREDICATE_MATCHES = 10

_JOIN = "FROM campsites c LEFT JOIN amenities a ON a.campsite_id = c.id"


def _fail(problems, entry_id, msg):
    problems.append("%s: %s" % (entry_id or "<no id>", msg))


def validate(path):
    """Return (problems, warnings, stats)."""
    with open(path) as fh:
        doc = yaml.safe_load(fh)

    problems = []
    warnings = []

    queries = doc.get("queries") or []
    vibes = doc.get("vibe_queries") or []
    if not isinstance(vibes, list):
        vibes = []

    seen_ids = set()
    seen_queries = {}
    known_item_count = 0
    referenced_ids = set()
    predicates = []

    for entry in list(queries) + list(vibes):
        if not isinstance(entry, dict):
            _fail(problems, None, "entry is not a mapping: %r" % (entry,))
            continue

        eid = entry.get("id")
        if not eid:
            _fail(problems, None, "entry has no id: %r" % (entry.get("query"),))
            continue
        if eid in seen_ids:
            _fail(problems, eid, "duplicate id; slugs must be stable and unique")
        seen_ids.add(eid)

        query = entry.get("query")
        if not query or not str(query).strip():
            _fail(problems, eid, "empty query")
        else:
            # A duplicated query string is not fatal, but it usually means a
            # copy-paste that will double-count in the metrics.
            key = str(query).strip().lower()
            if key in seen_queries:
                warnings.append(
                    "%s: same query text as %s (%r)" % (eid, seen_queries[key], query)
                )
            seen_queries[key] = eid

        kind = entry.get("kind")
        if kind == "known_item":
            cid = entry.get("expect_campsite_id")
            if not isinstance(cid, int):
                _fail(problems, eid, "known_item needs an integer expect_campsite_id")
            else:
                known_item_count += 1
                referenced_ids.add(cid)
        elif kind == "predicate":
            sql = entry.get("must_sql")
            if not sql or not str(sql).strip():
                _fail(problems, eid, "predicate needs must_sql")
            else:
                predicates.append((eid, str(sql)))
            at = entry.get("precision_at")
            if not isinstance(at, int) or at < 1:
                _fail(problems, eid, "predicate needs a positive integer precision_at")
        elif kind == "graded":
            expect = entry.get("expect_any_of") or []
            reject = entry.get("reject_ids") or []
            if not expect and not reject:
                _fail(problems, eid, "graded entry needs expect_any_of or reject_ids")
            overlap = set(expect) & set(reject)
            if overlap:
                _fail(problems, eid,
                      "ids in both expect_any_of and reject_ids: %s" % sorted(overlap))
            for cid in list(expect) + list(reject):
                if not isinstance(cid, int):
                    _fail(problems, eid, "non-integer campsite id %r" % (cid,))
                else:
                    referenced_ids.add(cid)
        else:
            _fail(problems, eid,
                  "unknown kind %r (expected known_item, predicate or graded)" % (kind,))

        if not entry.get("why"):
            warnings.append("%s: no 'why'; an entry nobody can justify gets deleted "
                            "in six months" % eid)

    # ---- everything below needs the database ----
    conn = get_connection()
    try:
        cur = conn.cursor()

        if referenced_ids:
            cur.execute("SELECT id FROM campsites WHERE id = ANY(%s)",
                        (sorted(referenced_ids),))
            present = {row[0] for row in cur.fetchall()}
            for cid in sorted(referenced_ids - present):
                problems.append("campsite id %d does not exist in the corpus" % cid)

        for eid, sql in predicates:
            try:
                cur.execute("SELECT count(*) %s WHERE %s" % (_JOIN, sql))
                count = cur.fetchone()[0]
            except Exception as exc:
                conn.rollback()
                _fail(problems, eid, "must_sql failed: %s" % str(exc).strip().splitlines()[0])
                continue
            if count == 0:
                _fail(problems, eid, "must_sql matches no campsites, so it can never pass")
            elif count < MIN_PREDICATE_MATCHES:
                warnings.append(
                    "%s: must_sql matches only %d campsites; precision@k will be "
                    "harsh and noisy" % (eid, count))

        cur.execute("SELECT count(*) FROM campsites")
        corpus = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM campsite_embeddings")
        embedded = cur.fetchone()[0]
        cur.close()
    finally:
        conn.close()

    stats = {
        "objective": len(queries),
        "vibe": len(vibes),
        "known_item": known_item_count,
        "predicate": len(predicates),
        "corpus": corpus,
        "embedded": embedded,
    }
    return problems, warnings, stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", default=DEFAULT_PATH, help="golden set path")
    args = ap.parse_args(argv)

    problems, warnings, stats = validate(args.file)

    print("Golden set: %s" % args.file)
    print("  objective queries   %d  (%d known-item, %d predicate)"
          % (stats["objective"], stats["known_item"], stats["predicate"]))
    print("  vibe queries        %d" % stats["vibe"])
    print("  corpus              %d campsites, %d embedded"
          % (stats["corpus"], stats["embedded"]))

    if stats["embedded"] < stats["corpus"]:
        print("  NOTE: %d campsites have no embedding, so the semantic retriever "
              "cannot return them" % (stats["corpus"] - stats["embedded"]))

    if warnings:
        print("\n%d warning(s):" % len(warnings))
        for w in warnings:
            print("  - %s" % w)

    if problems:
        print("\n%d problem(s):" % len(problems))
        for p in problems:
            print("  - %s" % p)
        print("\nFAIL")
        return 1

    if stats["vibe"] == 0:
        print("\nUsable for regression testing, but there are no vibe queries, so "
              "this set cannot tell you whether the search got BETTER, only that "
              "it did not get worse.")

    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
