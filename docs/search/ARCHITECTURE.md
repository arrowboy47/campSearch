# Search Improvements: Architecture

Branch: `search-improvements`. Written 2026-10-06.

## Verified environment

| Fact | Value | How verified |
|---|---|---|
| Campsites | 2630 | live query |
| Sources | fs_usda 1061, thedyrt 555, reserve_california 512, ridb 326, californiasbestcamping 176 | live query |
| Postgres | 16.14, image `postgres:16` | live query + compose file |
| pgvector | **not available**, needs `pgvector/pgvector:pg16` | `pg_available_extensions` |
| Embeddings | `nomic-embed-text`, Jadu `:9091`, OpenAI-compatible | live request |
| Dimension | **768** | live request |
| Batching | supported | 3 inputs returned 3 vectors |
| Throughput | ~21 ms/doc batched, ~83 ms single | benchmark on host |
| Full corpus embed | ~55 s | 2630 x 21 ms |

Re-embedding the whole corpus takes under a minute, so "what text do we embed"
is cheap to iterate on. That is the hyperparameter most likely to move
relevance, and it costs nothing to try variants.

## Pipeline shape

Today: filter in SQL, then score survivors on name similarity.

Target: **retrieve from several sources in parallel, fuse, then rank.**

```
query + filters
      |
      +-- structured prefilter (SQL)      -> allowed id set
      |
      +-- lexical retriever (BM25)        -> top-K ids + scores
      +-- semantic retriever (pgvector)   -> top-K ids + scores
      +-- name matcher (existing)         -> top-K ids + scores
      |
   fusion (weighted, RankingConfig)       -> candidate set (~200-500)
      |
   ranking: + popularity + personalization + unknown-attribute penalty
      |
   results  -> impressions logged with position
```

Why this shape rather than extending the current loop: the current loop drops
anything failing the name gate, which would eat every semantic match. Retrieval
and ranking have to be separate stages for a non-name query to survive at all.

### Structured prefilter: hard vs soft

Hard filter (SQL `WHERE`) only when the user explicitly set the control AND the
attribute is trustworthy. Measured null rates decide the second half:

| Attribute | Null % | Treatment |
|---|---|---|
| `activities` | 0% | hard |
| `elevation_ft` | 0.04% | hard (handle negatives: 14 Death Valley sites) |
| lat/lon | 1.1% | hard |
| `terrain` | 1.2% | hard |
| `water` | 25.9% | soft boost |
| `toilet_type` | 35.2% | soft boost |
| `is_free` / `fee_min` | 34.7% / 36.2% | soft until backfilled |
| `water_feature` | 47.5% | soft boost |
| `forest_name` | 59.7% | forest-scoped search only |

Three-state semantics everywhere: `TRUE` matches, `FALSE` excludes when the
user asked for the attribute, `NULL` stays in the set and takes a configurable
ranking penalty. This replaces the current `am.water = TRUE` pattern, which
silently equates unknown with absent.

### Lexical retriever (BM25)

In-process Python (`rank_bm25`), not Postgres. Postgres has no native BM25;
`ts_rank` is a weaker, different function, and a real implementation would mean
a second extension (ParadeDB `pg_search`) on top of pgvector. At 2630 documents
an in-memory index is a trivial footprint, rebuilt on boot and after each
refresh run, and it is far easier to unit-test and tune than SQL ranking.
Revisit only if the corpus grows by an order of magnitude.

Indexed text per campsite: name, forest name, overview, and structured
attributes rendered into a sentence. The exact composition is a tunable.

### Semantic retriever (pgvector)

- Migration adds `CREATE EXTENSION vector` and
  `campsite_embeddings(campsite_id PK, embedding vector(768), source_text_hash,
  model, updated_at)`.
- `source_text_hash` means re-embedding only touches changed rows.
- Index: `hnsw` with cosine ops. Cosine and inner product rank identically for
  L2-normalized vectors (which nomic produces), so the distance metric is made
  configurable but is not expected to matter. The parameters that do matter are
  the embedded text composition and the candidate pool size.
- An embedding job runs after the scrape chain, as a new Airflow task.

### Provider abstraction

One interface (`embeddings.embed(texts) -> list[vector]`) with a base-URL and
model name from config. Local llama.cpp in dev, OpenAI-compatible endpoint in
prod, no code change between them. This mirrors the swappable-contractor
principle already used elsewhere in this stack.

## Data model

### Event log (the foundation)

`user_events`, append-only, retained indefinitely.

```
id            bigserial
user_id       int null        -- null for anonymous
anon_id       uuid null       -- cookie, 30 days, merged on signup
session_id    uuid
event_type    text            -- see below
campsite_id   int null
position      int null        -- rank in the result list, for impressions/clicks
query_text    text null
filters       jsonb null
result_ids    int[] null
meta          jsonb
created_at    timestamptz
```

Event types: `search_performed`, `result_impression`, `result_clicked`,
`campsite_viewed` (with dwell in `meta`), `saved` / `unsaved`,
`collection_added`, `review_submitted`, `search_feedback`.

Impressions store the **raw position**. Position-bias weighting (a skipped
result low in the list is weaker negative evidence than one at the top) is
applied at derivation time, never baked into the stored row, so the decay curve
can be re-tuned later without having destroyed the data.

Volume estimate: one search with 20 impressions is ~22 rows. At 1000
searches/day that is ~8M rows/year, a few GB with indexes. Comfortably inside
the available terabyte, so raw retention forever is affordable.

### Ratings and reviews

```
reviews(id, user_id, campsite_id, verdict bool, body text, created_at,
        status: pending|published|removed, UNIQUE(user_id, campsite_id))
review_photos(id, review_id, path, created_at, status)
review_attribute_reports(id, review_id, campsite_id, attribute, claimed_value,
                         created_at)
```

`verdict` is the thumbs up/down headline. The structured popup writes the body
plus any attribute corrections.

### Attribute corrections

Never overwrite scraped data directly: the weekly scrapers would clobber the
edit anyway. Corrections live in their own layer and are applied through a
gate: auto-apply once at least N distinct users report the same value with at
least X% agreement (N and X in config, starting at 3 and 75%), and every
auto-apply still lands in the admin queue for review. A correction that has not
met threshold is still visible to ranking as a soft negative signal on that
attribute's confidence.

### Derived affinity

`user_campsite_affinity(user_id, campsite_id, score, components jsonb,
computed_at)`, rebuilt by a scheduled job from `user_events`. Never written
inline. Starting weights (all configurable): rating thumbs-up strongest,
collection-add and save next, review-written next, click weak positive,
instant bounce weak negative, skipped impression very weak negative scaled by
position.

### Ranking config and evaluation

`RankingConfig` is a single versioned object holding every weight. Eval runs
write to `search_eval_runs(config_json, metrics_json, git_sha, created_at)`.
Metrics: nDCG@10, MRR, recall@50 against a golden query set. The golden set is
seeded by hand and then grows automatically from `search_feedback` events, so
the feedback UI is what makes the system measurable, not just a nicety.

No MLflow at this stage. It solves experiment tracking for model training
runs, and there is no trained model yet; a Postgres table holds the same
information with no new service and no homelab dependency in production.
Revisit when learning-to-rank training begins.

## Risks

- **pgvector image swap** touches the live database container. Mitigated by a
  `pg_dump` first and a persistent named volume.
- **Restructuring `search.py`** is the core of Phase 2. The 174 existing tests
  cover `_build_where`, `_name_score` and `_popularity_bonus` as units, but
  nothing covers end-to-end search, so end-to-end tests must land before the
  rewrite, not after.
- **Review abuse.** Reviews publish immediately, photos are held for approval,
  slurs are blocked at submission, and everything is reportable. The admin
  panel is therefore a hard dependency of shipping reviews.
- **EXIF in uploaded photos** leaks GPS coordinates. Stripping is mandatory.
- **Cold start.** Personalization contributes nothing until a user has history,
  and collaborative filtering needs real volume. Both sit behind config weights
  that start at zero, so the system degrades to today's behavior rather than
  producing nonsense.
- **Sparse, skewed feedback.** Thumbs on results will be low-volume and
  negative-biased. Used for evaluation first; only fed into ranking once the
  volume justifies it.
