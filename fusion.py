"""Reciprocal rank fusion for combining the search retrievers.

Three retrievers produce candidates for a query: name/fuzzy matching, BM25
lexical, and embedding similarity. Their scores are not comparable. A BM25
score is max-normalized per result set, so its top hit is 1.0 whatever it
actually scored; a cosine similarity is on an absolute scale; a name score is
a 0-100 fuzzy ratio. Measured on the real corpus, the raw BM25 top score runs
from about 5 to about 10 depending on the query, which means a normalized 1.0
can represent either a confident match or a vague one.

Reciprocal rank fusion sidesteps all of that by reading only the rank. Each
retriever contributes `weight / (k + rank)` to every document it returns, and
the fused score is the sum. A document that several retrievers rank highly
beats one that a single retriever loves, which is the behaviour we want from a
hybrid search, and no retriever needs its scores calibrated against another's.

The cost is that RRF discards magnitude: it cannot tell a retriever's rank-1
runaway winner from its rank-1 near-tie. That is an acceptable trade while the
weights are unmeasured guesses. Revisit it with real numbers once the P2-02
eval harness exists, not before.
"""

from typing import Dict, Iterable, List, Sequence, Tuple


def competition_ranks(scored: Iterable[Tuple[object, float]]) -> List[Tuple[object, int]]:
    """Assign 1-based competition ranks, giving equal scores the same rank.

    This matters more than it looks. Naive enumeration of a sorted list turns
    two documents a retriever scored *identically* into rank 1 and rank 2,
    which RRF then treats as a real difference. The ordering between them came
    from nothing but the order the rows arrived in, so an arbitrary database
    row order would leak into the final ranking and ties could never be broken
    by anything downstream. Sharing a rank keeps a tie a tie.

    Ranks are "competition" style (1, 2, 2, 4): tied documents share the lower
    rank and the next distinct score skips ahead, so a document's rank is
    always its true position in the ordering.

    Args:
        scored: (id, score) pairs. Need not be sorted.

    Returns:
        (id, rank) pairs sorted best rank first.
    """
    ordered = sorted(scored, key=lambda pair: -pair[1])
    out: List[Tuple[object, int]] = []
    prev_score = None
    prev_rank = 0
    for index, (doc_id, score) in enumerate(ordered, start=1):
        if prev_score is not None and score == prev_score:
            rank = prev_rank
        else:
            rank = index
        out.append((doc_id, rank))
        prev_score, prev_rank = score, rank
    return out


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Tuple[float, Iterable]],
    k: int = 60,
) -> List[Tuple[object, float]]:
    """Fuse several retrievers' rankings into one.

    Args:
        ranked_lists: a sequence of (weight, items) pairs. `items` is either
            an iterable of IDs already in descending relevance order, or an
            iterable of (id, score) pairs, in which case equal scores share a
            rank via `competition_ranks`. Prefer the (id, score) form whenever
            the retriever has real scores: it is the only form that can
            express a tie. A retriever that found nothing contributes nothing.
        k: the RRF constant. Each retriever contributes weight / (k + rank).
            Larger k flattens the decay, so deeper results keep more
            influence and the gap between adjacent ranks narrows.

    Returns:
        A list of (id, fused_score) sorted by fused score descending. Equal
        fused scores are returned in order of first appearance across the
        input lists, so the output is deterministic and a caller can break
        those ties on its own criteria.

    Raises:
        ValueError: if k is negative, which would let a rank-1 document divide
            by zero or score negatively.
    """
    if k < 0:
        raise ValueError("rrf_k must be >= 0, got %r" % (k,))

    scores: Dict[object, float] = {}
    # First-seen order, used only to break ties deterministically.
    first_seen: Dict[object, int] = {}
    counter = 0

    for weight, items in ranked_lists:
        if not weight:
            # A zero weight disables a retriever outright. Skip it rather than
            # adding zeros, so it cannot introduce documents that only it
            # found and that nothing then ranks.
            continue

        items = list(items)
        if items and isinstance(items[0], tuple):
            ranked = competition_ranks(items)
        else:
            ranked = [(doc_id, rank) for rank, doc_id in enumerate(items, start=1)]

        for doc_id, rank in ranked:
            if doc_id not in first_seen:
                first_seen[doc_id] = counter
                counter += 1
            scores[doc_id] = scores.get(doc_id, 0.0) + weight / (k + rank)

    return sorted(
        scores.items(),
        key=lambda item: (-item[1], first_seen[item[0]]),
    )
