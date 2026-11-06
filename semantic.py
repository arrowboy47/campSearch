"""Semantic retriever for campsites, over the pgvector embeddings.

Embeds the query with the same model and the same text conventions the
embedding job used, then asks Postgres for the nearest campsite vectors by
cosine distance. The vectors live in `campsite_embeddings` (migration 0026,
`vector(768)` behind an HNSW cosine index); `scripts/embed_campsites.py` fills
them.

**Scores here are raw cosine similarity, deliberately not normalized.** This
is the opposite choice from `lexical.py`, which max-normalizes so its top hit
is always 1.0. Cosine similarity is already on a fixed, absolute scale, so
normalizing it would throw away the one thing it has that BM25 does not: a
score that means the same thing from one query to the next. The fusion step
must not assume the two retrievers' scores are comparable. They are not, which
is why fusion is rank-based. See `docs/search/BUILD_PLAN.md`, P2-06.

**On the missing task prefixes.** `nomic-embed-text` was trained with
`search_document: ` and `search_query: ` prefixes, and the embedding job used
neither. Queries here therefore also use neither, because a prefix on only one
side would compare a prefixed query against unprefixed documents and quietly
degrade every result. Adding prefixes to both sides is a plausible improvement
but it means re-embedding all 2630 documents, so it waits for the P2-02 eval
harness to say whether it actually helps. Do not add a prefix to just this
side.
"""

from typing import List, Optional, Tuple

from db import get_connection
from embeddings import EmbeddingError, EmbeddingProvider
from ranking_config import DEFAULT_CONFIG, RankingConfig


# An interactive query gets one attempt and a short timeout. The provider's
# own defaults (300s, 3 retries with exponential backoff) are sized for the
# batch embedding job; on a request path they turn an unreachable embedding
# service into a three-second hang on every single search, which is exactly
# what happened when the retrievers were first wired in.
QUERY_TIMEOUT_SECONDS = 2
QUERY_MAX_RETRIES = 1


# Nearest-neighbour SQL. The ORDER BY is written on the distance operator
# rather than on the computed similarity so that the HNSW index can serve it;
# ordering by "1 - (...)" descending is algebraically the same but planners do
# not recognise it as an index-ordered scan.
_KNN_SQL = """
    SELECT campsite_id, 1 - (embedding <=> %s::vector) AS similarity
    FROM campsite_embeddings
    ORDER BY embedding <=> %s::vector
    LIMIT %s
"""


def embed_query(query: str, provider: Optional[EmbeddingProvider] = None) -> Optional[List[float]]:
    """Embed a single query string, or return None if the service is unusable.

    Args:
        query: the raw user query. No task prefix is added; see the module
            docstring for why.
        provider: an EmbeddingProvider, or None to construct the default one.

    Returns:
        A 768-float vector, or None when the query is empty or the embedding
        service could not be reached.
    """
    if not query or not query.strip():
        return None

    if provider is None:
        try:
            provider = EmbeddingProvider(
                timeout=QUERY_TIMEOUT_SECONDS,
                max_retries=QUERY_MAX_RETRIES,
            )
        except Exception:
            return None

    try:
        vectors = provider.embed([query.strip()])
    except EmbeddingError:
        # The embedding service being down must degrade semantic search to
        # nothing, not take the whole search page with it. Lexical and name
        # retrieval still work without it.
        return None
    except Exception:
        return None

    return vectors[0] if vectors else None


def search(
    query: str,
    limit: Optional[int] = None,
    config: RankingConfig = DEFAULT_CONFIG,
    provider: Optional[EmbeddingProvider] = None,
) -> List[Tuple[int, float]]:
    """Retrieve campsites semantically similar to the query.

    Args:
        query: the user's text query.
        limit: how many neighbours to return. Defaults to the config's
            semantic_candidates, which is the retrieval depth, not the number
            of results a user sees.
        config: RankingConfig.
        provider: an EmbeddingProvider, or None for the default.

    Returns:
        A list of (campsite_id, cosine_similarity) sorted most similar first,
        or an empty list if the query could not be embedded. Similarity runs
        from 1.0 (identical direction) down, and is NOT normalized.
    """
    if limit is None:
        limit = config.semantic_candidates

    vector = embed_query(query, provider=provider)
    if vector is None:
        return []

    # psycopg2 has no vector adapter, so the literal is passed as text and
    # cast in SQL. Still a bound parameter, not interpolation.
    literal = "[" + ",".join(repr(float(x)) for x in vector) + "]"

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(_KNN_SQL, (literal, literal, int(limit)))
        rows = cur.fetchall()
        cur.close()
    finally:
        # A leaked connection here would be held for the life of the request
        # worker, so close on the error path too.
        conn.close()

    return [(cid, float(sim)) for cid, sim in rows]
