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
    """

    popularity_weight: float = 2.2
    popularity_cap: float = 8.0
    fuzzthresh: int = 62
    result_limit: int = 200
    candidate_hard_limit: int = 5000
    unknown_attr_penalty: float = 0.0

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
            f"{self.candidate_hard_limit}|{self.unknown_attr_penalty}"
        )
        digest = hashlib.sha256(field_str.encode()).hexdigest()
        return digest[:16]


DEFAULT_CONFIG = RankingConfig()
