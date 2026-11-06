"""Lexical retriever for campsites using BM25 ranking.

This module provides in-process BM25-based lexical search for campsites. We use
in-process indexing rather than Postgres full-text search because:

- Postgres lacks native BM25; ts_rank is a different and weaker function
- In-process indexing is far easier to tune and test than SQL ranking

Measured against the real 2630-document corpus on 2026-10-09: the index takes
2.8 seconds to build and costs about 130 MB of resident memory, and a query
returns in 5 to 15 ms. The build is per-process and lazy, so the first search
after a restart pays the 2.8 seconds. That is fine for one dev process and is
the thing to revisit before running multiple workers.

Scores are max-normalized within a result set, which means the top hit is
always 1.0 whatever its raw score. That discards BM25's cross-query
calibration (raw top scores range from about 5 to 10 across queries), so the
fusion step must not read a normalized score as a confidence. See P2-06.
"""

import re
from typing import List, Tuple
from rank_bm25 import BM25Okapi
from ranking_config import DEFAULT_CONFIG, RankingConfig
from db import get_connection


def _tokenize(text: str, min_token_length: int = 2) -> List[str]:
    """Tokenize text for BM25 indexing.

    Lowercases, splits on non-alphanumerics, and filters out tokens shorter
    than min_token_length. Does not stem (stemming mangles proper nouns, and
    the name matcher already handles variations).

    Args:
        text: text to tokenize
        min_token_length: minimum token length to keep (default 2)

    Returns:
        list of tokens
    """
    if not text:
        return []
    # Lowercase and split on non-alphanumerics
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    # Filter out very short tokens
    return [t for t in tokens if len(t) >= min_token_length]


class LexicalIndex:
    """BM25 index for lexical campsite retrieval.

    Holds tokenized documents and a BM25 model, enabling fast lexical search.
    Scores are normalized to 0..1 within each result set so they can be fused
    with other ranking signals.

    Attributes:
        campsite_ids: list of campsite IDs in document order
        bm25: BM25Okapi model
        config: RankingConfig for k1, b, min_token_length
    """

    def __init__(
        self,
        campsite_ids: List[int],
        tokenized_docs: List[List[str]],
        config: RankingConfig = DEFAULT_CONFIG
    ):
        """Initialize index from tokenized documents.

        Args:
            campsite_ids: list of campsite IDs, one per document
            tokenized_docs: list of tokenized document (list of tokens)
            config: RankingConfig for BM25 parameters
        """
        self.campsite_ids = campsite_ids
        self.config = config
        # BM25 model with tunable k1 and b parameters
        self.bm25 = BM25Okapi(
            tokenized_docs,
            k1=config.bm25_k1,
            b=config.bm25_b
        )

    def search(
        self,
        query: str,
        limit: int = None
    ) -> List[Tuple[int, float]]:
        """Search the index and return top results.

        Args:
            query: search query string
            limit: maximum number of results to return. Defaults to the
                config's result_limit so the retriever and the rest of the
                ranking stack agree on how wide the candidate set is.

        Returns:
            list of (campsite_id, normalized_score) tuples, sorted by score
            descending. Scores are normalized to 0..1 within the result set.
        """
        if limit is None:
            limit = self.config.result_limit

        # Tokenize query using same tokenizer as documents
        query_tokens = _tokenize(query, self.config.bm25_min_token_length)

        # Empty query returns empty list
        if not query_tokens:
            return []

        # Get raw BM25 scores for all documents
        raw_scores = self.bm25.get_scores(query_tokens)

        # Find documents with non-zero scores
        scored_docs = [
            (idx, score)
            for idx, score in enumerate(raw_scores)
            if score > 0
        ]

        # Sort by score descending
        scored_docs.sort(key=lambda x: x[1], reverse=True)

        # Limit results
        scored_docs = scored_docs[:limit]

        # Normalize scores to 0..1
        if scored_docs:
            max_score = scored_docs[0][1]
            if max_score > 0:
                normalized = [
                    (self.campsite_ids[idx], score / max_score)
                    for idx, score in scored_docs
                ]
            else:
                # All zero scores (shouldn't happen with our filter)
                normalized = [(self.campsite_ids[idx], 0.0) for idx, _ in scored_docs]
        else:
            normalized = []

        return normalized


def build_index(
    rows: List[Tuple],
    config: RankingConfig = DEFAULT_CONFIG
) -> LexicalIndex:
    """Build a BM25 index from campsite rows.

    Takes raw database rows and builds an in-memory BM25 index. The row
    format matches that returned by scripts.embed_campsites.compose_document:
    (id, name, forest_name, terrain, activities, water_feature, toilet_type,
     is_free, reservation_type, elevation_ft, overview).

    Args:
        rows: list of campsite row tuples
        config: RankingConfig for BM25 parameters

    Returns:
        LexicalIndex ready for search
    """
    from scripts.embed_campsites import compose_document

    campsite_ids = []
    tokenized_docs = []

    for row in rows:
        campsite_id = row[0]
        # Reuse the same document composer as embeddings use
        doc = compose_document(row)
        tokens = _tokenize(doc, config.bm25_min_token_length)

        campsite_ids.append(campsite_id)
        tokenized_docs.append(tokens)

    return LexicalIndex(campsite_ids, tokenized_docs, config)


# Module-level cached index, built lazily on first access
_cached_index = None


def get_index(config: RankingConfig = DEFAULT_CONFIG) -> LexicalIndex:
    """Get or lazily build the global BM25 index from the database.

    The index is built once and cached for the lifetime of the process.
    This function is safe to call at module import time: it will not
    touch the database until actually called.

    Args:
        config: RankingConfig for BM25 parameters

    Returns:
        LexicalIndex for the entire campsite corpus
    """
    global _cached_index

    # Only one index is cached. A caller that alternates between configs
    # rebuilds every call, at 2.8 seconds a time; that is acceptable for the
    # eval harness and would not be for a request path.
    if _cached_index is not None and _cached_index.config == config:
        return _cached_index

    # Build index from database
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                c.id, c.name, c.forest_name, c.terrain, a.activities,
                a.water_feature, a.toilet_type, c.is_free, c.reservation_type,
                c.elevation_ft, c.overview
            FROM campsites c
            LEFT JOIN amenities a ON a.campsite_id = c.id
            ORDER BY c.id
            """
        )
        rows = cur.fetchall()
        cur.close()

    _cached_index = build_index(rows, config)
    return _cached_index
