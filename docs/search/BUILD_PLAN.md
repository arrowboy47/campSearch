# Search Improvements: Build Plan

Branch: `search-improvements`. Written 2026-10-06.
Phase 1 only. Phases 2 to 4 are outlined at the bottom and get their own tasks
once Phase 1 lands.

## Locked state shape

Contractors may not add fields to these without architect approval. Full
definitions in `ARCHITECTURE.md`.

- `user_events(id, user_id, anon_id, session_id, event_type, campsite_id,
  position, query_text, filters, result_ids, meta, created_at)`
- `reviews(id, user_id, campsite_id, verdict, body, status, created_at)`
- `review_photos(id, review_id, path, status, created_at)`
- `review_attribute_reports(id, review_id, campsite_id, attribute,
  claimed_value, created_at)`
- Event types: `search_performed`, `result_impression`, `result_clicked`,
  `campsite_viewed`, `saved`, `unsaved`, `collection_added`,
  `review_submitted`, `search_feedback`
- Review status values: `pending`, `published`, `removed`

Migrations continue from `0023`. Every task that changes the DB adds a
migration; none edit an existing one.

---

## Task 01: End-to-end search tests before any rewrite

Add `tests/test_search_endtoend.py` covering the CURRENT behavior of
`search_campsites` so the Phase 2 rewrite has a safety net. Use a fake row
fixture, no live DB. Cover: no-query alphabetical path, the forest-substring
branch (`search.py:228`), the `fuzzthresh` drop, popularity tie-breaking, and
multi-filter interaction through `_build_where`.

Verify: the suite must pass unmutated AND fail under each of these
mutations applied one at a time (restore with `git checkout -- search.py`
after each):
`s/fuzzthresh=62/fuzzthresh=30/`,
`s/_POPULARITY_CAP = 8.0/_POPULARITY_CAP = 800.0/`,
`s/len(forest_hits) > name_substr/len(forest_hits) >= name_substr/`,
`s/if len(nq) >= 4:/if len(nq) >= 2:/`
Done when: baseline exits 0 and all four mutations exit 1.

## Task 02: Fix NULL-as-false in filters (P0)

Migration `0023` is not needed; this is code only. Rework `_build_where`
(`search.py:107`) so an unknown value is not silently excluded. For `water`,
`toilet`, `reservable`, `is_open`: a requested filter keeps rows where the
value is TRUE **or NULL**, and returns the NULL ones flagged so ranking can
penalize them. Add `unknown_attrs` to each returned row.

Verify: `python -m pytest tests/test_search_filters.py tests/test_search_endtoend.py -q`
Done when: requesting `water=true` returns both confirmed-water and
unknown-water sites, with the unknown ones marked, and tests assert it.

## Task 02b: Surface attribute confidence in the UI

The consequence of Task 02. Admitting NULL rows means a filtered result set
now mixes confirmed matches with unknowns, and today they look identical. The
`reservable` filter returns 2421 of 2630 rows, so without a visible
distinction the filter reads as broken rather than honest.

Every row already carries `unknown_attrs` from Task 02. Render it: a small
muted chip on any result card and campsite page where a *requested* attribute
is unknown, worded for the attribute ("cost unknown", "water unknown",
"reservations unknown"). Never show a chip for an attribute the user did not
filter on. Confirmed matches get no chip at all, so the absence of a chip is
itself the signal and the common case stays visually quiet.

Also add a one-line count above the results ("1649 results, 912 with unknown
cost") when any row in the set carries an unknown for a requested attribute.

Shared partial, used by both `templates/results.html` and the campsite page.
Styling follows the existing chip pattern already used for land type and
reservation type. Must work in light and dark themes and at the 640 and 820
breakpoints.

Deliberately NOT in this task: the ranking penalty for unknowns. That lands in
Phase 2 with `RankingConfig`, so the weight lives with every other tunable
instead of becoming another magic constant.

Verify: `python -m pytest tests/test_unknown_attr_ui.py -q`
Done when: a filtered search mixing confirmed and unknown rows renders chips
only on the unknown ones and only for requested attributes, the count line
appears only when unknowns exist, and no chip renders for an unfiltered
attribute.

## Task 03: Backfill fee data for ridb and reserve_california (P0)

New `scripts/backfill_fees.py`. For the 838 sites where `is_free` and
`fee_min` are NULL (all `ridb` and `reserve_california` rows), derive both from
existing fee text where parseable, reusing the fee parser already covered by
`tests/test_fee_parser.py`. Write a `scrape_runs` row. Idempotent. Report how
many of the 838 resolved.

Verify: `python scripts/backfill_fees.py --dry-run && python -m pytest tests/test_fee_parser.py -q`
Done when: a dry run prints per-source resolved/unresolved counts and changes
nothing; a real run lowers the `is_free` null rate below 35% and is safe to
re-run.

## Task 04: Event log schema

Migration `0023_user_events.sql` creating `user_events` exactly as locked
above, with indexes on `(user_id, created_at)`, `(anon_id, created_at)`,
`(campsite_id, event_type)`, and `(event_type, created_at)`. Append-only: no
update or delete paths.

Verify: `python scripts/migrate.py && python -c "import db; c=db.get_connection().cursor(); c.execute(\"select count(*) from user_events\"); print(c.fetchone())"`
Done when: the migration applies cleanly, is recorded in `schema_migrations`,
and the table accepts an insert with a null `user_id`.

## Task 05: Anonymous identity cookie

Signed `anon_id` cookie (UUID), 30-day lifetime, set on first request that
lacks one. On signup or login, rewrite that visitor's `user_events.anon_id`
rows to the new `user_id` and clear the cookie. Reuse the existing Flask
session signing key from `config.py`.

Verify: `python -m pytest tests/test_anon_identity.py -q`
Done when: an anonymous visit logs events under `anon_id`, signing up
reassigns those rows to the account, and no event is orphaned.

## Task 06: Event capture, server side

`db.log_event(**fields)`, best-effort like the existing `record_pick`
(`db.py:278`): it must swallow all errors so tracking can never break a page.
Wire `search_performed` (query, filters, result ids, result count) into the
`/results` route and `campsite_viewed` into the campsite route. Keep
`pick_count` working; `result_clicked` is additional, not a replacement.

Verify: `python -m pytest tests/test_event_logging.py -q`
Done when: a search writes exactly one `search_performed` row with its filters
as jsonb, and a DB error during logging does not surface to the user.

## Task 07: Event capture, client side

Extend `initPickTracking` (`main.js:475`) into a small event module. Send
`result_impression` for every rendered result card with its 1-based position,
batched in one request per page render, and `result_clicked` with position on
click. Keep `navigator.sendBeacon` with the `fetch` fallback. No-JS visitors
simply produce no events.

Verify: `python -m pytest tests/test_event_api.py -q`
Done when: rendering 20 results produces 20 impression rows carrying distinct
positions, and clicking one adds a `result_clicked` row with a matching
position.

## Task 08: Ratings and reviews schema

Migration `0024_reviews.sql` creating `reviews`, `review_photos` and
`review_attribute_reports` as locked above, with
`UNIQUE(user_id, campsite_id)` on `reviews`.

Verify: `python scripts/migrate.py`
Done when: applied cleanly and a second review by the same user for the same
campsite is rejected by the constraint.

## Task 09: Review submission

Account required. Thumbs up/down headline on the campsite page, with a modal
for the body text and the structured attribute questions. Rate limit 3 reviews
per user per day. Slur and hate-speech blocklist rejects at submission with a
clear message; ordinary profanity passes. Reviews publish immediately with
status `published`. CSRF protection as used by the existing POST routes.

Verify: `python -m pytest tests/test_reviews.py -q`
Done when: an anonymous POST is rejected, a logged-in POST publishes, a
blocklisted term is rejected, and the fourth submission in a day is refused.

## Task 10: Review photos

Reuse the avatar upload path (`static/uploads/`, 3MB cap). Up to 4 photos per
review. **Strip all EXIF on upload**, since phone photos carry GPS. Photos are
created with status `pending` and do not appear publicly until approved.
Thumbnails generated at upload.

Verify: `python -m pytest tests/test_review_photos.py -q`
Done when: an upload with GPS EXIF is stored with no EXIF, oversized and
non-image files are rejected, and a pending photo is absent from the public
campsite page.

## Task 11: Review display

Reviews section at the bottom of the campsite page: aggregate thumbs
up/down count, then published reviews with approved photos, newest first,
paginated at 20. "Have you visited here? Leave a review." prompt for signed-in
users without one, and the existing auth-gate prompt for anonymous visitors.

Verify: `python -m pytest tests/test_review_display.py -q`
Done when: only `published` reviews and `approved` photos render, and the
prompt varies correctly by auth state and whether the user already reviewed.

## Task 12: Admin panel

`is_admin` flag on `users` (migration `0025`). `/admin` behind that flag,
404 for everyone else. Queues for: reported reviews, pending photos, and
pending attribute corrections. Actions: approve, remove, dismiss. Every action
writes an audit row.

Verify: `python -m pytest tests/test_admin.py -q`
Done when: a non-admin gets 404 on every admin route, an admin can approve a
photo and remove a review, and each action is audited.

## Task 13: Attribute corrections pipeline

Corrections from reviews land in `review_attribute_reports`. A job applies one
only when at least N distinct users report the same value with at least X%
agreement (config, defaulting to 3 and 75%), and every auto-apply still enters
the admin queue. Scraped values are never edited in place; the correction layer
is consulted at read time.

Verify: `python -m pytest tests/test_attribute_corrections.py -q`
Done when: 2 agreeing reports do not apply, 3 at 100% do, 3 at 50% do not, and
a subsequent scrape does not revert an applied correction.

## Task 14: Search feedback UI

Optional, unobtrusive. Per-result thumbs and one query-level "did you find what
you wanted?" on `/results`, both writing `search_feedback` events with the
query, filters and position. Never blocking, never a modal.

Verify: `python -m pytest tests/test_search_feedback.py -q`
Done when: feedback writes an event carrying enough context to reconstruct the
query, and dismissing it writes nothing.

## Task 15: Phase 1 suite and docs

Run everything. Update `~/Documents/Notes/Projects/campsearch/Roadmap.md` and
`Log.md`, and append to `Reference/Guides/Stack Log.md` per the vault rules.

Verify: `python -m pytest -q`
Done when: the full suite passes with no DB or network, and the vault reflects
what shipped.

---

## A note on verifying test tasks

The first attempt at Task 01 passed `pytest -q` while failing to detect four
of the five behaviors it claimed to pin. Mutation testing caught it.

**For any task whose deliverable is tests, `Verify:` must be mutation based.**
Name the specific mutations, require the suite to fail under each, and require
the source file to be restored afterwards. "The tests pass" only proves the
tests run; it says nothing about whether they constrain anything.

**Always run mutation tests with `PYTHONDONTWRITEBYTECODE=1`, or clear
`__pycache__` between runs.** Python validates cached bytecode by source mtime
and size. A mutation like `fuzzthresh=62` to `fuzzthresh=30` changes neither,
so if the restore lands in the same second the mutated run compiled, the
interpreter keeps serving bytecode built from the mutated source. This
actually happened on 2026-10-06: it made a clean checkout appear to fail, and
it silently invalidated a whole round of mutation results. Assert the mutated
value is really live (read it back with `inspect.signature`) rather than
trusting that editing the file was enough.

## Later phases (not yet tasked)

**Phase 2, retrieval.** pgvector migration and embedding job; `rank_bm25`
index; retrieve-then-fuse restructure of `search.py`; `RankingConfig`; golden
set and eval harness writing to `search_eval_runs`.

**Phase 3, personalization.** Derived affinity job; content-based scoring;
item-item collaborative filtering skeleton, switched on by config once volume
allows.

**Phase 4, separate branch.** Conversational RAG assistant.
