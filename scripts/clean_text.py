#!/usr/bin/env python3
"""Normalize the messy scraped prose fields into something a page can show.

The scrapers store whatever the source gave them. For fs.usda that means the
`fee` field is often several paragraphs of pass-discount policy where the real
price is the first line, and `overview` / `seasons_of_use` carry stray "Overview"
labels and non-breaking spaces.

This job rewrites, for every campsite:
  - fee      -> a short canonical string  ("Single $25 · Double $50 / night")
  - fee_min  -> lowest nightly site price in dollars (NULL if unknown)
  - fee_max  -> highest nightly site price
  - is_free  -> True when the source explicitly says there is no fee
  - overview / seasons_of_use -> whitespace + label cleanup, in place

It always parses from `fee_raw` (the untouched original snapshotted by migration
0012), so it is safe to run repeatedly and safe to improve the parser and re-run.

    python scripts/clean_text.py            # rewrite everything
    python scripts/clean_text.py --dry-run  # show a sample of before/after
    python scripts/clean_text.py --limit 30

The parse_fee / clean_prose helpers are importable so the scrapers can call them
at write time too.
"""

import re
import argparse

from _pipeline import get_conn, scrape_run


# --- whitespace / label cleanup -------------------------------------------------

_LABEL_PREFIXES = ("overview", "description", "general description")


def clean_prose(text):
    """Collapse whitespace, drop nbsp, strip a leading 'Overview'-style label."""
    if not text:
        return None
    s = text.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    # leading label on its own line or followed by a colon
    stripped = s.lstrip()
    low = stripped.lower()
    for lab in _LABEL_PREFIXES:
        if low.startswith(lab):
            rest = stripped[len(lab):]
            # label may be followed by a separator, or run straight into the
            # first word ("OverviewSawtooth Canyon Campground...")
            if rest[:1] in (":", "\n", " ", "") or rest[:1].isupper():
                s = rest.lstrip(": \n")
                break
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip() or None


# --- fee parsing --------------------------------------------------------------

_MONEY = r"\$\s?(\d+(?:\.\d{1,2})?)"

# "Single Site: $25 per night", "Double Site: $50", "Group Site $99"
_SITE_LINE = re.compile(
    r"(single|double|group|family|equestrian|walk[- ]?in|standard|tent|rv|cabin)\s*"
    r"site\s*:?\s*" + _MONEY,
    re.I,
)
_VEHICLE = re.compile(r"additional vehicle fee\s*:?\s*" + _MONEY, re.I)
# a bare "$34 per night" / "$16 /site/night" / "$10 per night per vehicle"
_NIGHTLY = re.compile(_MONEY + r"\s*(?:/|per)\s*(?:site\s*/?\s*)?night", re.I)

_FREE_PATTERNS = (
    re.compile(r"^\s*no\s*\.?\s*$", re.I),
    re.compile(r"^\s*free\b", re.I),
    re.compile(r"no fee", re.I),
    re.compile(r"no fees? (?:are|is) (?:required|associated|charged)", re.I),
    re.compile(r"free of charge", re.I),
)

# once one of these shows up the line has stopped quoting a price and started
# quoting policy — anything after it is noise for our purposes
_PROSE_MARKERS = re.compile(
    r"operat(?:es?|ing)|concessionaire|interagency|america the beautiful"
    r"|senior (?:and|&) access|following fees|pass(?:es)? (?:are|is) honored"
    r"|reservation (?:charge|fee) is added|special[- ]use permit|managed by",
    re.I,
)

_LABEL_ORDER = ["single", "standard", "tent", "rv", "double", "family",
                "equestrian", "walk-in", "walkin", "group", "cabin"]


def _fmt_amount(a):
    return f"${a:g}"


def parse_fee(raw):
    """(display, fee_min, fee_max, is_free) from a raw scraped fee string.

    display is None when nothing usable was found (caller can fall back to raw).
    """
    if raw is None:
        return None, None, None, None
    text = raw.replace("\xa0", " ").strip()
    if not text:
        return None, None, None, None

    if any(p.search(text) for p in _FREE_PATTERNS) and "$" not in text:
        return "Free", 0.0, 0.0, True

    # Only look at the part before the policy prose kicks in.
    marker = _PROSE_MARKERS.search(text)
    head = text[: marker.start()] if marker else text

    labelled = {}
    for m in _SITE_LINE.finditer(head):
        key = re.sub(r"[ -]", "", m.group(1).lower())
        labelled.setdefault(key, float(m.group(2)))

    vehicle = None
    vm = _VEHICLE.search(head)
    if vm:
        vehicle = float(vm.group(1))

    nightly = [float(m.group(1)) for m in _NIGHTLY.finditer(head)]

    site_amounts = list(labelled.values()) or nightly
    if not site_amounts:
        # last resort: first dollar figure anywhere in the head
        loose = re.search(_MONEY, head)
        if loose:
            site_amounts = [float(loose.group(1))]

    if not site_amounts:
        return None, None, None, False

    fee_min = min(site_amounts)
    fee_max = max(site_amounts)

    # Build a compact display string.
    parts = []
    if labelled:
        seen = set()
        for key in _LABEL_ORDER:
            if key in labelled and key not in seen:
                seen.add(key)
                parts.append(f"{key.capitalize()} {_fmt_amount(labelled[key])}")
        for key, amt in labelled.items():
            if key not in seen:
                parts.append(f"{key.capitalize()} {_fmt_amount(amt)}")
        display = " · ".join(parts) + " / night"
    elif fee_min == fee_max:
        display = f"{_fmt_amount(fee_min)} / night"
    else:
        display = f"{_fmt_amount(fee_min)}–{_fmt_amount(fee_max)} / night"

    if vehicle:
        display += f" · +{_fmt_amount(vehicle)}/vehicle"

    return display, fee_min, fee_max, False


# The Dyrt fees are already tidy ("$5", "$75–$180", "Free"): keep the string,
# just pull min/max out of it.
_DYRT_RANGE = re.compile(r"\$(\d+(?:\.\d+)?)\s*(?:[–-]\s*\$?(\d+(?:\.\d+)?))?")


def parse_clean_fee(raw):
    if not raw:
        return None, None, None, None
    s = raw.strip()
    if s.lower() in ("free", "free.", "no", "no fee"):
        return "Free", 0.0, 0.0, True
    m = _DYRT_RANGE.search(s)
    if not m:
        return s, None, None, None
    lo = float(m.group(1))
    hi = float(m.group(2)) if m.group(2) else lo
    disp = f"${lo:g}" if lo == hi else f"${lo:g}–${hi:g}"
    return disp, lo, hi, (lo == 0.0)


# --- runner -----------------------------------------------------------------

SELECT_SQL = """
    SELECT id, source, COALESCE(fee_raw, fee) AS raw, overview, seasons_of_use
    FROM campsites
    ORDER BY id
"""

UPDATE_SQL = """
    UPDATE campsites
    SET fee = %(fee)s, fee_min = %(fmin)s, fee_max = %(fmax)s, is_free = %(free)s,
        overview = %(overview)s, seasons_of_use = %(seasons)s
    WHERE id = %(id)s
"""


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    with get_conn() as conn, scrape_run("clean_text") as run:
        sel = conn.cursor()
        q = SELECT_SQL + (f" LIMIT {int(args.limit)}" if args.limit else "")
        sel.execute(q)
        rows = sel.fetchall()
        sel.close()

        upd = conn.cursor()
        shown = 0
        for cid, source, raw, overview, seasons in rows:
            run.seen += 1

            if source == "thedyrt":
                fee_disp, fmin, fmax, free = parse_clean_fee(raw)
            else:
                fee_disp, fmin, fmax, free = parse_fee(raw)

            # Keep a trimmed version of the raw text when we could not parse a
            # price, rather than blanking the field.
            if fee_disp is None and raw:
                first = clean_prose(raw)
                fee_disp = (first.split("\n", 1)[0][:120] if first else None)

            new_overview = clean_prose(overview)
            new_seasons = clean_prose(seasons)

            params = {
                "id": cid, "fee": fee_disp, "fmin": fmin, "fmax": fmax,
                "free": free, "overview": new_overview, "seasons": new_seasons,
            }

            if args.dry_run:
                if shown < 40 and raw and (raw or "").strip() != (fee_disp or ""):
                    print(f"[{cid}] {source}")
                    print(f"   raw : {raw[:160]!r}")
                    print(f"   fee : {fee_disp!r}  min={fmin} max={fmax} free={free}")
                    shown += 1
                continue

            upd.execute(UPDATE_SQL, params)
            run.upserted += 1

        if not args.dry_run:
            conn.commit()
        upd.close()


if __name__ == "__main__":
    main()
