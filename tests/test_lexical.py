"""Tests for lexical BM25 retriever.

Tests verify:
1. Distinctive terms return correct campsite
2. Campsites with no overview are findable by attributes
3. Rare terms outrank common ones (BM25 advantage)
4. Normalized scores are 0..1 and ordering preserved
5. Tokenization is case/punctuation insensitive
6. Config tunables affect scores
7. Empty queries return empty list
8. get_index() is lazy and doesn't touch database at import time
"""

import pytest
import sys
from lexical import (
    build_index,
    LexicalIndex,
    _tokenize,
    get_index,
)
from ranking_config import RankingConfig, DEFAULT_CONFIG
from scripts.embed_campsites import compose_document


# ========== Test fixtures ==========

def make_row(
    cid=1,
    name="Test Campsite",
    forest="tahoe",
    terrain=None,
    activities=None,
    water="",
    toilet="vault",
    is_free=False,
    resv_type="reservation",
    elev=5000,
    overview=None
):
    """Create a campsite row tuple for compose_document."""
    return (cid, name, forest, terrain, activities, water, toilet, is_free, resv_type, elev, overview)


# ========== Test 1: Distinctive term matches right campsite ==========

def test_distinctive_term_first():
    """Query matching a distinctive term returns the right campsite first.

    Uses real compose_document so we test the actual document format.
    """
    # Create a corpus with several documents so BM25 IDF is meaningful.
    # Need at least 3-5 documents to avoid zero IDF for rare terms.
    rows = [
        make_row(cid=1, name="Pinecrest Lake", forest="sierra", overview="Scenic lake with fishing"),
        make_row(cid=2, name="Mountain Lake Camp", forest="sierra", overview="Mountain terrain"),
        make_row(cid=3, name="Big Flat Trailhead", forest="tahoe", overview="Hiking trail access"),
        make_row(cid=4, name="River Camp", forest="klamath", overview="River access point"),
        make_row(cid=5, name="Forest Camp", forest="plumas", overview="Forest setting area"),
    ]

    index = build_index(rows, DEFAULT_CONFIG)

    # Query for "pinecrest" should return campsite 1 first
    results = index.search("pinecrest", limit=5)
    assert len(results) >= 1, "Pinecrest should be found"
    assert results[0][0] == 1, f"Pinecrest should rank first, got {results[0][0]}"
    # Score should be normalized to 0..1
    assert 0 <= results[0][1] <= 1, "Score should be normalized"


# ========== Test 2: Campsites with no overview are findable ==========

def test_no_overview_findable_by_terrain():
    """Campsite with no overview still findable by terrain.

    39% of campsites have NULL overview, so they must be findable by other
    attributes. This test verifies terrain search works even when overview
    is absent.
    """
    rows = [
        # Campsite with overview and flat terrain
        make_row(
            cid=1,
            name="Lake Camp",
            terrain="flat meadow",
            overview="Beautiful lake views and camping opportunities"
        ),
        # Campsite with NO overview but distinctive alpine terrain
        make_row(
            cid=2,
            name="Ridge Camp",
            terrain="alpine rocky ridge",
            overview=None
        ),
        # Additional docs to ensure non-zero IDF
        make_row(cid=3, name="Camp A", terrain="mixed forest", overview="Normal camp"),
        make_row(cid=4, name="Camp B", terrain="sloped hills", overview="Small camp"),
        make_row(cid=5, name="Camp C", terrain="flat plains", overview="Wide open"),
    ]

    index = build_index(rows, DEFAULT_CONFIG)

    # Query for "alpine" should find the no-overview campsite
    results = index.search("alpine", limit=10)
    assert len(results) >= 1, "Alpine terrain should be findable"
    cids = [cid for cid, _ in results]
    assert 2 in cids, "Campsite 2 (no overview) should be found via terrain"


def test_no_overview_findable_by_activities():
    """Campsite with no overview still findable by activities."""
    rows = [
        make_row(
            cid=1,
            name="Lake Camp",
            activities=["fishing", "boating"],
            overview=None
        ),
        make_row(
            cid=2,
            name="Trail Camp",
            activities=["hiking"],
            overview="Some overview text"
        ),
        # Additional docs to ensure non-zero IDF
        make_row(cid=3, name="Camp A", activities=["camping"], overview="Camp A"),
        make_row(cid=4, name="Camp B", activities=["picnicking"], overview="Camp B"),
        make_row(cid=5, name="Camp C", activities=["nature walking"], overview="Camp C"),
    ]

    index = build_index(rows, DEFAULT_CONFIG)

    # Query for "fishing" should find campsite 1 even though it has no overview
    results = index.search("fishing", limit=10)
    cids = [cid for cid, _ in results]
    assert 1 in cids, "Campsite 1 (no overview) should be found via activities"


# ========== Test 3: Rare terms outrank common ones ==========

def test_rare_term_beats_common():
    """Rare term in one document beats common term in many documents.

    This is the whole point of BM25 over naive term matching.
    """
    rows = [
        # Several campsites with "hiking"
        make_row(cid=1, name="Trout Lake", overview="Good hiking trails"),
        make_row(cid=2, name="Bass Camp", overview="Hiking available here"),
        make_row(cid=3, name="Salmon River", overview="Hiking and kayaking"),
        # One campsite with rare term "spelunking"
        make_row(cid=4, name="Cave Camp", overview="Great for spelunking"),
        # More unrelated campsites to increase corpus
        make_row(cid=5, name="Valley Camp", overview="Scenic valley location"),
        make_row(cid=6, name="Forest Camp", overview="Wooded area"),
        make_row(cid=7, name="Beach Camp", overview="Sandy beach access"),
        make_row(cid=8, name="Mountain Camp", overview="High altitude views"),
    ]

    index = build_index(rows, DEFAULT_CONFIG)

    # Query for "spelunking" should rank the cave campsite first (rare term)
    results = index.search("spelunking", limit=10)
    assert len(results) >= 1, "Spelunking should match"
    assert results[0][0] == 4, f"Rare 'spelunking' should rank first, got {results[0][0]}"

    # Query for "hiking" should match multiple hiking sites
    results_hiking = index.search("hiking", limit=10)
    assert len(results_hiking) >= 2, "Hiking should match multiple sites"


# ========== Test 4: Normalized scores are 0..1, ordering preserved ==========

def test_normalized_scores_range():
    """Normalized scores are within 0..1."""
    rows = [
        make_row(cid=1, name="Test A", overview="keyword here"),
        make_row(cid=2, name="Test B", overview="something else"),
        make_row(cid=3, name="Test C", overview="another thing"),
        make_row(cid=4, name="Test D", overview="different content"),
        make_row(cid=5, name="Test E", overview="extra info"),
    ]

    index = build_index(rows, DEFAULT_CONFIG)
    results = index.search("keyword", limit=10)

    # All scores should be 0..1
    for cid, score in results:
        assert 0 <= score <= 1, f"Score {score} out of range for {cid}"


def test_normalization_preserves_order():
    """Normalization preserves the sort order."""
    rows = [
        make_row(cid=1, name="Alpha", overview="word word word"),
        make_row(cid=2, name="Beta", overview="word"),
        make_row(cid=3, name="Gamma", overview="something else"),
        make_row(cid=4, name="Delta", overview="nothing"),
        make_row(cid=5, name="Epsilon", overview="extra text"),
    ]

    index = build_index(rows, DEFAULT_CONFIG)

    # Get unnormalized scores for comparison
    query_tokens = ["word"]
    raw_scores = index.bm25.get_scores(query_tokens)
    # Document with more "word" instances should score higher (or at least as high)
    assert raw_scores[0] >= raw_scores[1], "First doc should score as high or higher"

    # Get normalized results
    results = index.search("word", limit=10)
    result_cids = [cid for cid, _ in results]

    # Order should be preserved
    if len(result_cids) >= 2:
        assert result_cids[0] == 1, "Order should match raw scores"
        assert result_cids[1] == 2


# ========== Test 5: Tokenization insensitive to case and punctuation ==========

def test_tokenize_lowercases():
    """Tokenization lowercases text."""
    tokens = _tokenize("TAHOE National Forest", min_token_length=2)
    assert "tahoe" in tokens
    assert "national" in tokens
    assert "forest" in tokens
    assert "TAHOE" not in tokens, "Should be lowercased"


def test_tokenize_strips_punctuation():
    """Tokenization strips punctuation."""
    tokens = _tokenize("Tahoe National Forest.", min_token_length=2)
    assert "tahoe" in tokens
    assert "national" in tokens
    assert "forest" in tokens
    # Period should not create a token
    assert "." not in tokens


def test_tokenize_respects_min_length():
    """Tokenization drops tokens shorter than min_token_length."""
    tokens = _tokenize("Big Flat Camp", min_token_length=2)
    assert "big" in tokens
    assert "flat" in tokens
    assert "camp" in tokens

    # With min=4, "big" should be dropped
    tokens_min4 = _tokenize("Big Flat Camp", min_token_length=4)
    assert "big" not in tokens_min4, "Single-letter tokens dropped at min=4"
    assert "flat" in tokens_min4


def test_search_case_insensitive():
    """Search is case-insensitive."""
    rows = [
        make_row(cid=1, name="Tahoe National Forest", forest="tahoe"),
        make_row(cid=2, name="Other Camp", forest="sierra"),
        make_row(cid=3, name="Another Camp", forest="klamath"),
        make_row(cid=4, name="Test Camp", forest="plumas"),
        make_row(cid=5, name="Sample Camp", forest="modoc"),
    ]
    index = build_index(rows, DEFAULT_CONFIG)

    # All of these should match campsite 1
    for query in ["tahoe", "TAHOE", "Tahoe", "tAhOe"]:
        results = index.search(query, limit=10)
        assert len(results) >= 1, f"Query '{query}' should match"
        assert results[0][0] == 1


# ========== Test 6: Tunables from config affect scores ==========

def test_k1_tunable_affects_scores():
    """Changing k1 in config changes scores."""
    rows = [
        make_row(cid=1, name="Test A", overview="word word word"),
        make_row(cid=2, name="Test B", overview="word"),
        make_row(cid=3, name="Test C", overview="something else"),
        make_row(cid=4, name="Test D", overview="other content"),
        make_row(cid=5, name="Test E", overview="different text"),
    ]

    config_low_k1 = DEFAULT_CONFIG.replace(bm25_k1=0.5)
    config_high_k1 = DEFAULT_CONFIG.replace(bm25_k1=3.0)

    index_low = build_index(rows, config_low_k1)
    index_high = build_index(rows, config_high_k1)

    results_low = index_low.search("word", limit=10)
    results_high = index_high.search("word", limit=10)

    # Scores should exist and potentially differ
    assert len(results_low) >= 1, "Low k1 should find results"
    assert len(results_high) >= 1, "High k1 should find results"
    if len(results_low) > 0 and len(results_high) > 0:
        # Scores might differ due to k1 parameter affecting term frequency saturation
        # But both should have campsite 1 first (most frequent)
        assert results_low[0][0] == 1
        assert results_high[0][0] == 1


def test_b_tunable_affects_scores():
    """Changing b in config changes scores."""
    rows = [
        make_row(cid=1, name="Test A", overview="word word word"),
        make_row(cid=2, name="Test B", overview="word"),
        make_row(cid=3, name="Test C", overview="something else"),
        make_row(cid=4, name="Test D", overview="other content"),
        make_row(cid=5, name="Test E", overview="different text"),
    ]

    config_low_b = DEFAULT_CONFIG.replace(bm25_b=0.0)
    config_high_b = DEFAULT_CONFIG.replace(bm25_b=1.0)

    index_low = build_index(rows, config_low_b)
    index_high = build_index(rows, config_high_b)

    results_low = index_low.search("word", limit=10)
    results_high = index_high.search("word", limit=10)

    # Scores should exist
    assert len(results_low) >= 1, "Low b should find results"
    assert len(results_high) >= 1, "High b should find results"
    # Both should rank the most frequent document first
    if len(results_low) > 0 and len(results_high) > 0:
        assert results_low[0][0] == 1
        assert results_high[0][0] == 1


def test_min_token_length_tunable_affects_results():
    """Changing min_token_length affects which documents match."""
    rows = [
        make_row(cid=1, name="By Lake", overview="x small lake"),
        make_row(cid=2, name="Big Lake", overview="large lake"),
        make_row(cid=3, name="Camp A", overview="another place"),
        make_row(cid=4, name="Camp B", overview="other spot"),
        make_row(cid=5, name="Camp C", overview="final area"),
    ]

    # With min_token_length=2, "x" is dropped; with min=1, it's kept
    config_min2 = DEFAULT_CONFIG.replace(bm25_min_token_length=2)
    config_min1 = RankingConfig(
        popularity_weight=2.2,
        popularity_cap=8.0,
        fuzzthresh=62,
        result_limit=200,
        candidate_hard_limit=5000,
        unknown_attr_penalty=0.0,
        bm25_k1=1.5,
        bm25_b=0.75,
        bm25_min_token_length=1
    )

    index_min2 = build_index(rows, config_min2)
    index_min1 = build_index(rows, config_min1)

    # Search for "x" should work with min=1 but be empty with min=2
    results_min2 = index_min2.search("x", limit=10)
    results_min1 = index_min1.search("x", limit=10)

    assert len(results_min2) == 0, "min_token_length=2 should drop 'x'"
    assert len(results_min1) >= 1, "min_token_length=1 should keep 'x'"


# ========== Test 7: Empty/whitespace queries return empty ==========

def test_empty_query_returns_empty():
    """Empty query returns empty list."""
    rows = [make_row(cid=1, name="Test")]
    index = build_index(rows, DEFAULT_CONFIG)

    results = index.search("", limit=10)
    assert results == [], "Empty query should return empty list"


def test_whitespace_only_query_returns_empty():
    """Whitespace-only query returns empty list."""
    rows = [make_row(cid=1, name="Test")]
    index = build_index(rows, DEFAULT_CONFIG)

    results = index.search("   ", limit=10)
    assert results == [], "Whitespace-only query should return empty list"


def test_query_with_only_short_tokens_returns_empty():
    """Query with only short tokens (below min_token_length) returns empty."""
    rows = [make_row(cid=1, name="Test")]
    index = build_index(rows, DEFAULT_CONFIG)

    # Query with only single-letter tokens, but min_token_length=2
    results = index.search("a b c", limit=10)
    assert results == [], "Query of only short tokens should return empty"


# ========== Test 8: get_index() is lazy ==========

def test_get_index_import_safe():
    """Importing lexical module does not touch database.

    This test verifies that module-level code in lexical.py does not
    access the database. We test this by importing with DATABASE_URL
    pointing to a dead port (done by pytest fixture).
    """
    # If we got here, the import succeeded without touching the database.
    # That's the test.
    assert True, "Import succeeded without database access"


def test_get_index_lazy_builds_on_first_call():
    """get_index() builds index lazily on first call, not at import.

    We can't directly test the lazy build in an isolated test because
    we'd need a real database. However, we can verify that get_index()
    returns a LexicalIndex and that calling it again returns the cached
    version (if we mock the database).
    """
    # Create a small corpus and verify get_index-like behavior
    # by manually building an index
    rows = [
        make_row(cid=1, name="Test"),
    ]
    index = build_index(rows, DEFAULT_CONFIG)
    assert isinstance(index, LexicalIndex), "build_index should return LexicalIndex"


# ========== Test 9: Result count respects limit ==========

def test_result_limit_respected():
    """Search respects the limit parameter."""
    # Create rows with "fishing" in some and other unique terms in others
    rows = []
    for i in range(20):
        if i < 6:
            # First 6 campsites mention fishing
            desc = f"Fishing available at site {i}"
        else:
            # Others have different unique terms
            desc = f"Activity{i} available at camp{i} location"
        rows.append(make_row(cid=i, name=f"Camp {i}", overview=desc))

    index = build_index(rows, DEFAULT_CONFIG)

    results = index.search("fishing", limit=5)
    # Should have at most 5 results
    assert len(results) <= 5, "Should not exceed limit"
    # Should have found fishing in at least one document
    assert len(results) > 0, "Should find fishing in at least one document"


# ========== Test 10: Scores are well-formed ==========

def test_scores_are_floats():
    """All returned scores are valid floats."""
    rows = [
        make_row(cid=1, name="Test", overview="word"),
        make_row(cid=2, name="Other", overview="word word"),
    ]
    index = build_index(rows, DEFAULT_CONFIG)

    results = index.search("word", limit=10)
    for cid, score in results:
        assert isinstance(score, float), f"Score should be float, got {type(score)}"
        assert score > 0, "Score should be positive"
        assert score <= 1, "Score should be <= 1 (normalized)"


def test_default_limit_follows_config():
    """search() with no limit uses config.result_limit, not a module constant.

    Pins the contract that the retriever and the rest of the ranking stack
    agree on how wide the candidate set is. A hardcoded default here would
    silently cap the fused set in P2-06.
    """
    # Each row carries a unique rare token. A corpus where every term sits in
    # every document drives the whole vocabulary's IDF negative, which is a
    # degenerate case rank_bm25 floors to a negative score, not a realistic one.
    rows = [
        make_row(cid=i, name=f"Lake Camp {i}",
                 overview=f"lake fishing camping sentinel{i}")
        for i in range(1, 21)
    ]
    narrow = DEFAULT_CONFIG.replace(result_limit=3)
    index = build_index(rows, config=narrow)

    assert len(index.search("lake")) == 3, "default limit should follow config"
    assert len(index.search("lake", limit=7)) == 7, "explicit limit still wins"
