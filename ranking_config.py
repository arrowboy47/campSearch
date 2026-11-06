"""Configuration for ranking and filtering parameters.

Contains all tunable knobs for search ranking (popularity weights, fuzzy thresholds,
result limits, etc.). Used by search.py, and by the eval harness to sweep
parameter values for performance testing.
"""

from dataclasses import dataclass, replace as dataclasses_replace
import hashlib


@dataclass(frozen=True)
class RankingConfig:
    """Immutable configuration for search ranking and filtering.

    Fields:
        popularity_weight: coefficient for log-scaled pick_count bonus.
        popularity_cap: hard ceiling on the popularity bonus score.
        fuzzthresh: minimum score to include a fuzzy match result.
        result_limit: maximum number of results returned.
        candidate_hard_limit: maximum candidate rows pulled from database.
        unknown_attr_penalty: penalty (unused, for future refinement).
        bm25_k1: BM25 k1 parameter, controls term frequency saturation.
        bm25_b: BM25 b parameter, controls length normalization (0..1).
        bm25_min_token_length: minimum token length for lexical indexing.
        name_score_bucket: width, in name-score points, of one relevance
            band. Name scores are quantized to this before ranking, so two
            names that differ only by a scoring artifact count as equally
            relevant and can be ordered by popularity instead. A 0-100 name
            score carries noise at the sub-point level: "Pinecrest Camp" and
            "Pinecrest Lodge" score 98.8 and 98.5 against "pinecrest" purely
            because one name is a character longer. Set to 0 to disable
            bucketing and rank on exact scores.
        semantic_candidates: how many nearest neighbours the semantic
            retriever asks pgvector for. Retrieval depth, not page size.
        lexical_candidates: how many hits the lexical retriever contributes
            to fusion.
        rrf_k: reciprocal rank fusion constant. Each retriever contributes
            weight / (rrf_k + rank). A larger k flattens the curve, so the
            retrievers' deeper results keep more influence; 60 is the value
            from the original RRF paper and a reasonable default.
        rrf_weight_name: fusion weight for the name/fuzzy retriever.
        rrf_weight_lexical: fusion weight for the BM25 retriever.
        rrf_weight_semantic: fusion weight for the embedding retriever.
    """

    popularity_weight: float = 2.2
    popularity_cap: float = 8.0
    fuzzthresh: int = 62
    result_limit: int = 200
    candidate_hard_limit: int = 5000
    unknown_attr_penalty: float = 0.0
    bm25_k1: float = 1.5
    bm25_b: float = 0.75
    bm25_min_token_length: int = 2
    name_score_bucket: float = 2.0
    semantic_candidates: int = 200
    lexical_candidates: int = 200
    # Rank-based fusion. These weights are starting points, not measured
    # values: nothing can tune them until the P2-02 eval harness and its
    # golden set exist. Name is weighted highest because an exact-name lookup
    # is the one query where the right answer is not a matter of taste, and
    # regressing it would be the most visible failure.
    rrf_k: int = 60
    rrf_weight_name: float = 1.5
    rrf_weight_lexical: float = 1.0
    rrf_weight_semantic: float = 1.0

    def replace(self, **kwargs):
        """Return a new RankingConfig with specified fields replaced.

        Since the dataclass is frozen, this provides a safe way to create
        variants for parameter sweeps without mutation.
        """
        return dataclasses_replace(self, **kwargs)

    def fingerprint(self):
        """Stable hash of the field values for eval run identification.

        Returns a 16-character hex string, suitable for storing in a results table
        to record exactly which config produced a metric.
        """
        field_str = (
            f"{self.popularity_weight}|{self.popularity_cap}|"
            f"{self.fuzzthresh}|{self.result_limit}|"
            f"{self.candidate_hard_limit}|{self.unknown_attr_penalty}|"
            f"{self.bm25_k1}|{self.bm25_b}|{self.bm25_min_token_length}|"
            f"{self.name_score_bucket}|"
            f"{self.semantic_candidates}|{self.lexical_candidates}|"
            f"{self.rrf_k}|{self.rrf_weight_name}|"
            f"{self.rrf_weight_lexical}|{self.rrf_weight_semantic}"
        )
        digest = hashlib.sha256(field_str.encode()).hexdigest()
        return digest[:16]


DEFAULT_CONFIG = RankingConfig()
