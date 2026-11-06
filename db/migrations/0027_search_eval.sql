-- 0027_search_eval
-- Human relevance judgments for search evaluation, and the queries they judge.
--
-- The point of these two tables is to make "is the search any good?" a number
-- instead of an argument. Every ranking tunable in ranking_config.py is
-- currently an unmeasured guess; a harness scoring a ranking against these
-- judgments is what turns a guess into a measurement.
--
-- search_eval_queries
--   query_text          - exactly what a user would type. UNIQUE, because two
--                         rows for the same text would split its judgments and
--                         silently halve the evidence for it.
--   intent              - free text: what the human expected to get back. Not
--                         machine-read; it exists so a judgment made in
--                         February can still be understood in August.
--   kind                - 'known_item' when exactly one campsite is correct and
--                         we know which (name lookups), scored by the rank of
--                         that one row. 'graded' when many answers are
--                         defensible, scored by nDCG over the judgments.
--   expect_campsite_id  - the single right answer for a known_item query.
--                         ON DELETE SET NULL rather than CASCADE: losing the
--                         campsite should not silently delete the query and
--                         every judgment hanging off it.
--   retired_at          - soft delete. A retired query drops out of scoring but
--                         keeps its judgments, so retiring a bad query never
--                         destroys human work that a future query could reuse.
--
-- search_judgments
--   One row per (query, campsite) pair, NOT per (query, campsite, ranking).
--   This is the whole efficiency argument for the design: a judgment is a
--   statement about whether a campsite answers a query, which does not change
--   when the ranking changes. So every config we try afterwards is scored
--   against labels that already exist, and a human only ever judges pairs
--   nobody has judged before.
--
--   grade               - 0 wrong / 1 acceptable / 2 ideal. Graded rather than
--                         binary because nDCG needs to reward putting an ideal
--                         result above a merely acceptable one; a binary label
--                         cannot express that and flattens the metric.
--   judged_by           - who judged it. ON DELETE SET NULL keeps the judgment
--                         when a user is deleted, since the label is still
--                         valid evidence even once its author is gone.
--
-- Constraints are declared inline. Postgres has no ADD CONSTRAINT IF NOT
-- EXISTS, so a separate ALTER would fail on a second apply and break
-- idempotency.

CREATE TABLE IF NOT EXISTS search_eval_queries (
    id                 SERIAL PRIMARY KEY,
    query_text         TEXT NOT NULL UNIQUE,
    intent             TEXT,
    kind               TEXT NOT NULL DEFAULT 'graded'
                       CHECK (kind IN ('graded', 'known_item')),
    expect_campsite_id INTEGER REFERENCES campsites(id) ON DELETE SET NULL,
    notes              TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    retired_at         TIMESTAMPTZ,

    -- A known_item query with no expected campsite cannot be scored, and a
    -- graded query with one implies a single right answer it does not have.
    CONSTRAINT search_eval_queries_kind_expect_ck CHECK (
        (kind = 'known_item' AND expect_campsite_id IS NOT NULL)
        OR (kind = 'graded' AND expect_campsite_id IS NULL)
    ),
    CONSTRAINT search_eval_queries_text_not_blank_ck CHECK (
        length(btrim(query_text)) > 0
    )
);

CREATE TABLE IF NOT EXISTS search_judgments (
    id          SERIAL PRIMARY KEY,
    query_id    INTEGER NOT NULL REFERENCES search_eval_queries(id) ON DELETE CASCADE,
    campsite_id INTEGER NOT NULL REFERENCES campsites(id) ON DELETE CASCADE,
    grade       SMALLINT NOT NULL CHECK (grade IN (0, 1, 2)),
    judged_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- One judgment per pair. The upsert in db.upsert_judgment relies on this
    -- to let a human change their mind without creating a second row that
    -- would double-count in the metrics.
    CONSTRAINT search_judgments_pair_uq UNIQUE (query_id, campsite_id)
);

CREATE INDEX IF NOT EXISTS search_judgments_query_idx
    ON search_judgments (query_id);

CREATE INDEX IF NOT EXISTS search_judgments_campsite_idx
    ON search_judgments (campsite_id);

-- Scoring only ever reads live queries, and there will be far more judgments
-- than queries, so keep the live set cheap to list.
CREATE INDEX IF NOT EXISTS search_eval_queries_live_idx
    ON search_eval_queries (id) WHERE retired_at IS NULL;
