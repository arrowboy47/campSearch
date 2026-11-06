"""Tests for the retrieve-then-fuse restructure of search_campsites (P2-06).

The central change: fuzzthresh no longer gates the result set. It used to
drop any row that failed a name match, which is why natural-language search
was absent rather than merely weak.
"""

import pytest

import search
from ranking_config import DEFAULT_CONFIG
from search import search_campsites


def _row(**kwargs):
    base = {
        "id": 1, "name": "Test", "forest_name": "inyo", "latitude": 37.0,
        "longitude": -119.0, "source": "fs_usda",
        "reservation_type": "reservation", "num_sites": 10, "fee": None,
        "fee_min": None, "is_free": False, "terrain": None,
        "elevation_ft": 5000, "pick_count": 0, "is_open": True,
        "forecast_json": None, "has_water": False, "has_restrooms": False,
        "toilet_type": None, "water_feature": None, "activities": [],
        "is_reservable": False, "forecast": None,
    }
    base.update(kwargs)
    return base


@pytest.fixture
def no_retrievers(monkeypatch):
    """Silence lexical and semantic unless a test opts in."""
    monkeypatch.setattr(search, "_lexical_ranked_ids", lambda q, a, c: [])
    monkeypatch.setattr(search, "_semantic_ranked_ids", lambda q, a, c: [])


# ========== the headline fix ==========

def test_semantic_only_hit_survives_fuzzthresh(monkeypatch):
    """A campsite whose name matches nothing must still be reachable.

    "Dogwood Flat" scores far below fuzzthresh against "somewhere quiet to
    fish", so the old pipeline dropped it outright no matter what the
    embeddings said. This is the bug that made natural-language search
    impossible rather than weak.
    """
    fixtures = [_row(id=1, name="Dogwood Flat")]
    monkeypatch.setattr(search, "_fetch_filtered", lambda f, hard_limit=None: fixtures)
    monkeypatch.setattr(search, "_lexical_ranked_ids", lambda q, a, c: [])
    monkeypatch.setattr(search, "_semantic_ranked_ids", lambda q, a, c: [1])

    results = search_campsites(query="somewhere quiet to fish")

    assert [r["id"] for r in results] == [1]
    assert results[0]["name_score"] == 0.0, (
        "it was found semantically, so it has no name score to report"
    )


def test_lexical_only_hit_survives_fuzzthresh(monkeypatch):
    fixtures = [_row(id=1, name="Dogwood Flat")]
    monkeypatch.setattr(search, "_fetch_filtered", lambda f, hard_limit=None: fixtures)
    monkeypatch.setattr(search, "_lexical_ranked_ids", lambda q, a, c: [1])
    monkeypatch.setattr(search, "_semantic_ranked_ids", lambda q, a, c: [])

    assert [r["id"] for r in search_campsites(query="vault toilet")] == [1]


def test_no_retriever_finds_it_so_it_is_absent(monkeypatch, no_retrievers):
    """The gate still works: an irrelevant row is not returned."""
    fixtures = [_row(id=1, name="Dogwood Flat")]
    monkeypatch.setattr(search, "_fetch_filtered", lambda f, hard_limit=None: fixtures)

    assert search_campsites(query="zzzzqqqq") == []


def test_retrievers_cannot_escape_the_structured_prefilter(monkeypatch):
    """A retriever returning an id the filters excluded must be ignored.

    Otherwise a semantic hit would smuggle a closed or wrongly-priced
    campsite past the user's filters.
    """
    fixtures = [_row(id=1, name="Allowed")]
    monkeypatch.setattr(search, "_fetch_filtered", lambda f, hard_limit=None: fixtures)
    # Retrievers run over the whole corpus, so they see ids 1 and 99.
    monkeypatch.setattr(search, "_semantic_ranked_ids",
                        lambda q, allowed, c: [i for i in (99, 1) if i in allowed])
    monkeypatch.setattr(search, "_lexical_ranked_ids", lambda q, a, c: [])

    assert [r["id"] for r in search_campsites(query="anything")] == [1]


# ========== degradation ==========

def test_lexical_failure_does_not_break_search(monkeypatch):
    """A broken lexical index must not take the search page down."""
    fixtures = [_row(id=1, name="Pinecrest Camp")]
    monkeypatch.setattr(search, "_fetch_filtered", lambda f, hard_limit=None: fixtures)
    monkeypatch.setattr(search, "_semantic_ranked_ids", lambda q, a, c: [])

    import lexical
    def _explode(*a, **k):
        raise RuntimeError("index is gone")
    monkeypatch.setattr(lexical, "get_index", _explode)

    assert [r["id"] for r in search_campsites(query="pinecrest")] == [1]


def test_semantic_failure_does_not_break_search(monkeypatch):
    fixtures = [_row(id=1, name="Pinecrest Camp")]
    monkeypatch.setattr(search, "_fetch_filtered", lambda f, hard_limit=None: fixtures)
    monkeypatch.setattr(search, "_lexical_ranked_ids", lambda q, a, c: [])

    import semantic as semantic_mod
    def _explode(*a, **k):
        raise RuntimeError("embeddings are gone")
    monkeypatch.setattr(semantic_mod, "search", _explode)

    assert [r["id"] for r in search_campsites(query="pinecrest")] == [1]


# ========== fusion is wired to the config ==========

def test_zero_weights_disable_the_retrievers(monkeypatch):
    """Weight 0 must stop the retriever being called at all, not just
    discount it. Calling a disabled retriever would still pay its latency.
    """
    fixtures = [_row(id=1, name="Pinecrest Camp")]
    monkeypatch.setattr(search, "_fetch_filtered", lambda f, hard_limit=None: fixtures)

    called = []
    monkeypatch.setattr(search, "_lexical_ranked_ids",
                        lambda q, a, c: called.append("lexical") or [])
    monkeypatch.setattr(search, "_semantic_ranked_ids",
                        lambda q, a, c: called.append("semantic") or [])

    config = DEFAULT_CONFIG.replace(rrf_weight_lexical=0.0, rrf_weight_semantic=0.0)
    search_campsites(query="pinecrest", config=config)
    assert called == []


def test_relative_weights_decide_between_disagreeing_retrievers(monkeypatch):
    """Name ranks 1 then 2, semantic ranks 2 then 1. The heavier one wins.

    Both retrievers have to rank both documents for this to test weighting
    rather than agreement: a document only one retriever returns loses to a
    document both return, whatever the weights, which is the whole point of
    RRF.
    """
    fixtures = [_row(id=1, name="Pinecrest Camp"), _row(id=2, name="Pinecrest Lodge")]
    monkeypatch.setattr(search, "_fetch_filtered", lambda f, hard_limit=None: fixtures)
    monkeypatch.setattr(search, "_lexical_ranked_ids", lambda q, a, c: [])
    monkeypatch.setattr(search, "_semantic_ranked_ids", lambda q, a, c: [2, 1])

    # Bucketing off, so the 98.8 / 98.5 name scores give a real name order.
    base = DEFAULT_CONFIG.replace(name_score_bucket=0.0)
    name_heavy = base.replace(rrf_weight_name=10.0, rrf_weight_semantic=1.0)
    sem_heavy = base.replace(rrf_weight_name=1.0, rrf_weight_semantic=10.0)

    assert search_campsites(query="pinecrest", config=name_heavy)[0]["id"] == 1
    assert search_campsites(query="pinecrest", config=sem_heavy)[0]["id"] == 2


def test_agreement_beats_a_single_retrievers_favourite(monkeypatch):
    """A document both retrievers rank beats one only semantic returns.

    This is what hybrid retrieval is bought for, and it holds even when
    semantic is weighted ten times higher.
    """
    fixtures = [_row(id=1, name="Pinecrest Camp"), _row(id=2, name="Dogwood Flat")]
    monkeypatch.setattr(search, "_fetch_filtered", lambda f, hard_limit=None: fixtures)
    monkeypatch.setattr(search, "_lexical_ranked_ids", lambda q, a, c: [])
    # id 2 is semantic's favourite; id 1 is also there, second.
    monkeypatch.setattr(search, "_semantic_ranked_ids", lambda q, a, c: [2, 1])

    sem_heavy = DEFAULT_CONFIG.replace(rrf_weight_name=1.0, rrf_weight_semantic=10.0)
    results = search_campsites(query="pinecrest", config=sem_heavy)
    assert results[0]["id"] == 1


def test_name_score_bucket_decides_what_counts_as_a_tie(monkeypatch):
    """With bucketing on, a 0.3-point name difference is a tie that
    popularity breaks. With it off, that artifact decides the order.
    """
    fixtures = [
        _row(id=1, name="Pinecrest Camp", pick_count=0),
        _row(id=2, name="Pinecrest Lodge", pick_count=500),
    ]
    monkeypatch.setattr(search, "_fetch_filtered", lambda f, hard_limit=None: fixtures)
    monkeypatch.setattr(search, "_lexical_ranked_ids", lambda q, a, c: [])
    monkeypatch.setattr(search, "_semantic_ranked_ids", lambda q, a, c: [])

    # 98.8 vs 98.5: same band at 2.0, so popularity breaks the tie.
    bucketed = search_campsites(query="pinecrest", config=DEFAULT_CONFIG)
    assert bucketed[0]["id"] == 2

    # Unbucketed, the 0.3 artifact is treated as a real difference.
    exact = DEFAULT_CONFIG.replace(name_score_bucket=0.0)
    assert search_campsites(query="pinecrest", config=exact)[0]["id"] == 1


# ========== reporting ==========

def test_relevance_is_rescaled_so_the_top_hit_is_100(monkeypatch, no_retrievers):
    """The popularity curve and the unknown-attribute penalty are calibrated
    against a 0-100 scale, not against a raw RRF score of about 0.02.
    """
    fixtures = [_row(id=1, name="Pinecrest Camp"), _row(id=2, name="Pinecrest Lodge")]
    monkeypatch.setattr(search, "_fetch_filtered", lambda f, hard_limit=None: fixtures)

    results = search_campsites(query="pinecrest")
    assert results[0]["relevance"] == pytest.approx(100.0)
    assert all(0.0 <= r["relevance"] <= 100.0 for r in results)


def test_unknown_attr_penalty_demotes_a_row(monkeypatch, no_retrievers):
    """The penalty is 0.0 by default, so prove it bites when turned up."""
    fixtures = [
        _row(id=1, name="Pinecrest Camp", toilet_type=None),
        _row(id=2, name="Pinecrest Lodge", toilet_type="vault"),
    ]
    monkeypatch.setattr(search, "_fetch_filtered", lambda f, hard_limit=None: fixtures)
    monkeypatch.setattr(search, "unknown_attrs_for",
                        lambda row, f: ("toilet",) if row["id"] == 1 else ())

    config = DEFAULT_CONFIG.replace(unknown_attr_penalty=50.0)
    results = search_campsites(query="pinecrest", config=config)
    assert results[0]["id"] == 2, "the row with an unknown attribute must drop"


def test_no_query_still_sorts_by_forest_then_name(monkeypatch, no_retrievers):
    """Browsing with filters and no query must not go through fusion."""
    fixtures = [
        _row(id=1, name="Zeta", forest_name="inyo"),
        _row(id=2, name="Alpha", forest_name="inyo"),
    ]
    monkeypatch.setattr(search, "_fetch_filtered", lambda f, hard_limit=None: fixtures)

    assert [r["id"] for r in search_campsites()] == [2, 1]


def test_retrievers_get_the_raw_query_not_the_normalized_one(monkeypatch, no_retrievers):
    """normalize() strips all whitespace so "pine crest" matches "Pinecrest".

    Handing that to a tokenizer would turn "lake fishing" into the single
    unknown token "lakefishing" and lose every lexical and semantic hit.
    """
    seen = {}
    monkeypatch.setattr(search, "_fetch_filtered",
                        lambda f, hard_limit=None: [_row(id=1, name="Fish Lake")])
    monkeypatch.setattr(search, "_lexical_ranked_ids",
                        lambda q, a, c: seen.setdefault("lexical", q) and [])
    monkeypatch.setattr(search, "_semantic_ranked_ids",
                        lambda q, a, c: seen.setdefault("semantic", q) and [])

    search_campsites(query="lake fishing")
    assert seen["lexical"] == "lake fishing"
    assert seen["semantic"] == "lake fishing"


def test_name_outweighs_semantic_by_default(monkeypatch):
    """Pins rrf_weight_name > rrf_weight_semantic in the shipped config.

    An exact-name lookup is the one query whose right answer is not a matter
    of taste, so when the retrievers disagree symmetrically the name has to
    win. At equal weights this is an exact tie and the outcome falls to
    first-appearance order, which is not a decision anyone made.
    """
    fixtures = [_row(id=1, name="Pinecrest Camp"), _row(id=2, name="Pinecrest Lodge")]
    monkeypatch.setattr(search, "_fetch_filtered", lambda f, hard_limit=None: fixtures)
    monkeypatch.setattr(search, "_lexical_ranked_ids", lambda q, a, c: [])
    monkeypatch.setattr(search, "_semantic_ranked_ids", lambda q, a, c: [2, 1])

    # Bucketing off so the name retriever gives a real order rather than a tie.
    config = DEFAULT_CONFIG.replace(name_score_bucket=0.0)
    assert search_campsites(query="pinecrest", config=config)[0]["id"] == 1
    assert DEFAULT_CONFIG.rrf_weight_name > DEFAULT_CONFIG.rrf_weight_semantic
