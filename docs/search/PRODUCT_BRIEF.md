# Search Improvements: Product Brief

Branch: `search-improvements`. Written 2026-10-06.

## What this is

A rebuild of CampSearch's search from name-matching into a ranked retrieval
system that understands natural language and learns from how people use it.

Three capabilities, in dependency order:

1. **A behavioral record.** Ratings, reviews, saves, searches, clicks and
   impressions, captured as an append-only event log.
2. **Hybrid retrieval.** Lexical (BM25) plus semantic (embeddings) search over
   campsite text, fused with the existing structured filters.
3. **Personalized ranking.** Content-based first, collaborative filtering once
   there is enough data to support it.

A conversational RAG assistant is explicitly out of scope here and belongs on
its own branch.

## Why now

Search today cannot answer the question people actually arrive with. Verified
against the live codebase on 2026-10-06:

- `_BASE_SQL` (`search.py:39`) selects 21 columns and `overview` is not one of
  them. **Descriptive text never enters retrieval.**
- `search_campsites` drops any row scoring under `fuzzthresh=62` on *campsite
  name* alone (`search.py:236`).

So "quiet lake campsite with easy hiking" cannot match anything, because no
campsite is named that. This is not a weak text search. There is no text
search. Adding one is the project.

Two data bugs compound it, both verified against the live database (2630 rows):

- Filters emit `am.water = TRUE`, so campsites with *unknown* values are
  silently excluded. `NULL = TRUE` is not true in SQL.
- `is_free` and `fee_min` are 0% populated for `ridb` (326 sites) and
  `reserve_california` (512 sites). Ticking "free only" removes 32% of the
  database, including every state park, with no indication to the user.

## Who it is for

Primary user is someone planning a California camping trip who knows roughly
what they want ("somewhere quiet, near water, not a long drive") but not which
campground that is. Secondary user is the site owner, who needs an admin
surface to keep user-contributed content clean.

This is also, explicitly, a learning project: the owner wants to build and
understand statistics and ML in a real application. Design choices favour being
legible and tunable over being maximally clever.

## Principles

- **Raw events are the source of truth.** Affinity scores are derived and
  recomputed, never written at interaction time. Changing a weight must be a
  recompute, not a data loss.
- **Hard filters only on what the user explicitly asked for.** System-inferred
  constraints become soft boosts. A wrong hard filter makes the right answer
  unreachable; a wrong boost only misorders it.
- **Unknown is not false.** Three-state attributes throughout.
- **Every tunable number lives in one config object.** No magic constants
  scattered through the ranking code.
- **Nothing ships unmeasured.** The eval harness lands with the retrieval work,
  not after it.

## V1 success criteria

- A natural-language query returns sensible results, measured by nDCG@10
  against a golden query set rather than by eyeballing.
- Every search, impression, click, save and review is logged with enough
  context to train a ranker later.
- A user can rate and review a campsite, with photos, and the owner can
  moderate it.
- "Free only" no longer hides a third of the database.

## Explicitly out of scope for V1

- Conversational / multi-turn RAG assistant (separate branch).
- A trained learning-to-rank model (needs months of logged data first).
- Collaborative filtering switched on (skeleton only; needs volume).
- MLflow or any experiment-tracking service. Eval runs go to a Postgres table
  until there is an actual model-training workload to justify more.
