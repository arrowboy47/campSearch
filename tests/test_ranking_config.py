"""Tests for the RankingConfig refactor.

Verifies:
1. Old module constants are gone from search.py
2. DEFAULT_CONFIG preserves current behavior (byte-identical results)
3. Config changes actually affect ranking and filtering
4. Explicit keyword arguments override config
5. Config is immutable
6. fingerprint() is stable and distinguishes configs
"""

import pytest
import math
from ranking_config import RankingConfig, DEFAULT_CONFIG
from search import search_campsites, _popularity_bonus


# ========== Test 1: Old constants are gone ==========

def test_old_constants_removed():
    """Verify _POPULARITY_WEIGHT and _POPULARITY_CAP no longer exist in search."""
    import search
    assert not hasattr(search, "_POPULARITY_WEIGHT"), (
        "_POPULARITY_WEIGHT must be removed entirely"
    )
    assert not hasattr(search, "_POPULARITY_CAP"), (
        "_POPULARITY_CAP must be removed entirely"
    )


# ========== Test 2: Defaults reproduce current behavior ==========

def test_default_config_values():
    """Verify DEFAULT_CONFIG has the expected field values."""
    assert DEFAULT_CONFIG.popularity_weight == 2.2
    assert DEFAULT_CONFIG.popularity_cap == 8.0
    assert DEFAULT_CONFIG.fuzzthresh == 62
    assert DEFAULT_CONFIG.result_limit == 200
    assert DEFAULT_CONFIG.candidate_hard_limit == 5000
    assert DEFAULT_CONFIG.unknown_attr_penalty == 0.0


def test_popularity_bonus_with_default_values(monkeypatch):
    """With default weight and cap, popularity bonus matches old behavior."""
    # Old behavior: min(8.0, 2.2 * log1p(100))
    # min(8.0, 2.2 * log1p(100)) = min(8.0, 2.2 * 4.6051...) = min(8.0, 10.131...)
    # Result: 8.0
    expected = min(8.0, 2.2 * math.log1p(100))

    result = _popularity_bonus(100, 2.2, 8.0)
    assert abs(result - expected) < 0.001

    # Verify it matches the default
    assert abs(result - 8.0) < 0.001


def test_search_with_default_config_respects_popularity_cap(monkeypatch):
    """With default config, popularity bonus is capped at 8.0.

    This is a mutation test: changing cap to 800 should flip the sort order.
    """

    def _make_campsite(**kwargs):
        base = {
            "id": 1,
            "name": "Test",
            "forest_name": "Test",
            "latitude": 37.0,
            "longitude": -119.0,
            "source": "fs_usda",
            "reservation_type": "reservation",
            "num_sites": 10,
            "fee": None,
            "fee_min": None,
            "is_free": False,
            "terrain": None,
            "elevation_ft": 5000,
            "pick_count": 0,
            "is_open": True,
            "forecast_json": None,
            "has_water": False,
            "has_restrooms": False,
            "toilet_type": None,
            "water_feature": None,
            "activities": [],
            "is_reservable": False,
            "forecast": None,
        }
        base.update(kwargs)
        return base

    # Row A: name exact match (100.0), pick_count=0 -> score=100.0+0=100.0
    # Row B: name fuzzy (91.2), pick_count=1000 ->
    #   with cap=8: score=91.2+8=99.2
    #   with cap=800: score=91.2+15.2=106.4
    fixtures = [
        _make_campsite(id=1, name="Trail", pick_count=0),
        _make_campsite(id=2, name="Mountain Trail", pick_count=1000),
    ]

    monkeypatch.setattr("search._fetch_filtered", lambda filters, hard_limit=None: fixtures)

    # With default config (cap=8.0)
    results_default = search_campsites(query="trail")
    assert len(results_default) >= 2
    assert results_default[0]["id"] == 1, "With cap=8, id=1 (score=100) should rank first"

    # With modified config (cap=800.0) - config argument
    config_high_cap = DEFAULT_CONFIG.replace(popularity_cap=800.0)
    results_high_cap = search_campsites(query="trail", config=config_high_cap)
    assert len(results_high_cap) >= 2
    assert results_high_cap[0]["id"] == 2, "With cap=800, id=2 (score=106.4) should rank first"


def test_search_with_default_fuzzthresh_boundary(monkeypatch):
    """Default fuzzthresh=62 is critical; test the boundary.

    A score of 45.7 is below 62, so should be dropped with default.
    """

    def _make_campsite(**kwargs):
        base = {
            "id": 1,
            "name": "Lakeshore Camp",
            "forest_name": "Inyo",
            "latitude": 37.0,
            "longitude": -119.0,
            "source": "fs_usda",
            "reservation_type": "reservation",
            "num_sites": 10,
            "fee": None,
            "fee_min": None,
            "is_free": False,
            "terrain": None,
            "elevation_ft": 5000,
            "pick_count": 0,
            "is_open": True,
            "forecast_json": None,
            "has_water": False,
            "has_restrooms": False,
            "toilet_type": None,
            "water_feature": None,
            "activities": [],
            "is_reservable": False,
            "forecast": None,
        }
        base.update(kwargs)
        return base

    fixtures = [_make_campsite()]
    monkeypatch.setattr("search._fetch_filtered", lambda filters, hard_limit=None: fixtures)

    # "Lakeshore Camp" vs "park" scores approximately 45.7
    results_default = search_campsites(query="park")
    assert len(results_default) == 0, "Score 45.7 < 62, should be dropped with default"

    # With explicit fuzzthresh=40, should pass
    results_lower = search_campsites(query="park", fuzzthresh=40)
    assert len(results_lower) >= 1, "Score 45.7 > 40, should pass with fuzzthresh=40"


def test_search_with_default_limit_is_200(monkeypatch):
    """Default limit is 200, not exceeded unless needed."""

    def _make_campsite(**kwargs):
        base = {
            "id": 1,
            "name": "Test",
            "forest_name": "Test",
            "latitude": 37.0,
            "longitude": -119.0,
            "source": "fs_usda",
            "reservation_type": "reservation",
            "num_sites": 10,
            "fee": None,
            "fee_min": None,
            "is_free": False,
            "terrain": None,
            "elevation_ft": 5000,
            "pick_count": 0,
            "is_open": True,
            "forecast_json": None,
            "has_water": False,
            "has_restrooms": False,
            "toilet_type": None,
            "water_feature": None,
            "activities": [],
            "is_reservable": False,
            "forecast": None,
        }
        base.update(kwargs)
        return base

    # Create 250 fixtures
    fixtures = [_make_campsite(id=i, forest_name=f"Forest{i}") for i in range(250)]
    monkeypatch.setattr("search._fetch_filtered", lambda filters, hard_limit=None: fixtures)

    # No query means sort by forest/name and limit to 200
    results = search_campsites(query=None)
    assert len(results) == 200, "Default limit=200 should cap results"


# ========== Test 3: Config changes affect behavior ==========

def test_config_fuzzthresh_changes_results(monkeypatch):
    """Changing fuzzthresh in config changes which rows pass."""

    def _make_campsite(**kwargs):
        base = {
            "id": 1,
            "name": "Test",
            "forest_name": "Test",
            "latitude": 37.0,
            "longitude": -119.0,
            "source": "fs_usda",
            "reservation_type": "reservation",
            "num_sites": 10,
            "fee": None,
            "fee_min": None,
            "is_free": False,
            "terrain": None,
            "elevation_ft": 5000,
            "pick_count": 0,
            "is_open": True,
            "forecast_json": None,
            "has_water": False,
            "has_restrooms": False,
            "toilet_type": None,
            "water_feature": None,
            "activities": [],
            "is_reservable": False,
            "forecast": None,
        }
        base.update(kwargs)
        return base

    fixtures = [_make_campsite(id=1, name="Lakeshore Camp")]
    monkeypatch.setattr("search._fetch_filtered", lambda filters, hard_limit=None: fixtures)

    # Config with high threshold
    config_high = DEFAULT_CONFIG.replace(fuzzthresh=80)
    results_high = search_campsites(query="park", config=config_high)
    assert len(results_high) == 0, "With high threshold, score 45.7 should be dropped"

    # Config with low threshold
    config_low = DEFAULT_CONFIG.replace(fuzzthresh=40)
    results_low = search_campsites(query="park", config=config_low)
    assert len(results_low) >= 1, "With low threshold, score 45.7 should pass"


def test_config_popularity_cap_changes_sort_order(monkeypatch):
    """Changing popularity_cap flips sort order (mutation test)."""

    def _make_campsite(**kwargs):
        base = {
            "id": 1,
            "name": "Test",
            "forest_name": "Test",
            "latitude": 37.0,
            "longitude": -119.0,
            "source": "fs_usda",
            "reservation_type": "reservation",
            "num_sites": 10,
            "fee": None,
            "fee_min": None,
            "is_free": False,
            "terrain": None,
            "elevation_ft": 5000,
            "pick_count": 0,
            "is_open": True,
            "forecast_json": None,
            "has_water": False,
            "has_restrooms": False,
            "toilet_type": None,
            "water_feature": None,
            "activities": [],
            "is_reservable": False,
            "forecast": None,
        }
        base.update(kwargs)
        return base

    fixtures = [
        _make_campsite(id=1, name="Trail", pick_count=0),
        _make_campsite(id=2, name="Mountain Trail", pick_count=1000),
    ]
    monkeypatch.setattr("search._fetch_filtered", lambda filters, hard_limit=None: fixtures)

    # With cap=8
    config_cap_8 = DEFAULT_CONFIG.replace(popularity_cap=8.0)
    results_cap_8 = search_campsites(query="trail", config=config_cap_8)
    assert results_cap_8[0]["id"] == 1

    # With cap=800
    config_cap_800 = DEFAULT_CONFIG.replace(popularity_cap=800.0)
    results_cap_800 = search_campsites(query="trail", config=config_cap_800)
    assert results_cap_800[0]["id"] == 2


# ========== Test 4: Explicit keywords override config ==========

def test_explicit_fuzzthresh_overrides_config(monkeypatch):
    """Explicit fuzzthresh kwarg overrides config value."""

    def _make_campsite(**kwargs):
        base = {
            "id": 1,
            "name": "Lakeshore Camp",
            "forest_name": "Inyo",
            "latitude": 37.0,
            "longitude": -119.0,
            "source": "fs_usda",
            "reservation_type": "reservation",
            "num_sites": 10,
            "fee": None,
            "fee_min": None,
            "is_free": False,
            "terrain": None,
            "elevation_ft": 5000,
            "pick_count": 0,
            "is_open": True,
            "forecast_json": None,
            "has_water": False,
            "has_restrooms": False,
            "toilet_type": None,
            "water_feature": None,
            "activities": [],
            "is_reservable": False,
            "forecast": None,
        }
        base.update(kwargs)
        return base

    fixtures = [_make_campsite()]
    monkeypatch.setattr("search._fetch_filtered", lambda filters, hard_limit=None: fixtures)

    # Config says fuzzthresh=80, but explicit kwarg says 40
    config_high = DEFAULT_CONFIG.replace(fuzzthresh=80)
    results = search_campsites(query="park", config=config_high, fuzzthresh=40)

    # Should use explicit 40, not config 80, so row passes
    assert len(results) >= 1, "Explicit fuzzthresh=40 should override config"


def test_explicit_limit_overrides_config(monkeypatch):
    """Explicit limit kwarg overrides config value."""

    def _make_campsite(**kwargs):
        base = {
            "id": 1,
            "name": "Test",
            "forest_name": "Test",
            "latitude": 37.0,
            "longitude": -119.0,
            "source": "fs_usda",
            "reservation_type": "reservation",
            "num_sites": 10,
            "fee": None,
            "fee_min": None,
            "is_free": False,
            "terrain": None,
            "elevation_ft": 5000,
            "pick_count": 0,
            "is_open": True,
            "forecast_json": None,
            "has_water": False,
            "has_restrooms": False,
            "toilet_type": None,
            "water_feature": None,
            "activities": [],
            "is_reservable": False,
            "forecast": None,
        }
        base.update(kwargs)
        return base

    fixtures = [_make_campsite(id=i, forest_name=f"Forest{i}") for i in range(100)]
    monkeypatch.setattr("search._fetch_filtered", lambda filters, hard_limit=None: fixtures)

    # Config says limit=50, explicit kwarg says 10
    config_50 = DEFAULT_CONFIG.replace(result_limit=50)
    results = search_campsites(query=None, config=config_50, limit=10)

    assert len(results) == 10, "Explicit limit=10 should override config limit=50"


# ========== Test 5: Immutability ==========

def test_config_is_frozen(monkeypatch):
    """RankingConfig is immutable; cannot mutate fields."""
    with pytest.raises(Exception):  # FrozenInstanceError or similar
        DEFAULT_CONFIG.popularity_cap = 999.0


def test_replace_returns_new_instance():
    """replace() returns a new instance, not a mutation."""
    original = DEFAULT_CONFIG
    modified = original.replace(popularity_cap=999.0)

    assert original.popularity_cap == 8.0
    assert modified.popularity_cap == 999.0
    assert original is not modified


# ========== Test 6: Fingerprint ==========

def test_fingerprint_is_stable():
    """Same config always produces same fingerprint."""
    config1 = RankingConfig(
        popularity_weight=2.2,
        popularity_cap=8.0,
        fuzzthresh=62,
        result_limit=200,
        candidate_hard_limit=5000,
        unknown_attr_penalty=0.0,
    )
    config2 = RankingConfig(
        popularity_weight=2.2,
        popularity_cap=8.0,
        fuzzthresh=62,
        result_limit=200,
        candidate_hard_limit=5000,
        unknown_attr_penalty=0.0,
    )

    fp1 = config1.fingerprint()
    fp2 = config2.fingerprint()
    assert fp1 == fp2


def test_fingerprint_differs_for_different_configs():
    """Different configs produce different fingerprints."""
    config1 = RankingConfig(popularity_cap=8.0)
    config2 = RankingConfig(popularity_cap=800.0)

    fp1 = config1.fingerprint()
    fp2 = config2.fingerprint()
    assert fp1 != fp2


def test_fingerprint_is_short():
    """Fingerprint is 16 hex characters (short and stable)."""
    fp = DEFAULT_CONFIG.fingerprint()
    assert len(fp) == 16
    assert all(c in "0123456789abcdef" for c in fp)
