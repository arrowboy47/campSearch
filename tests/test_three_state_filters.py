"""Integration tests for three-state filter semantics.

This file verifies that search_campsites attaches unknown_attrs to all
returned rows across all return paths.
"""

import pytest

from search import search_campsites


def _make_campsite(
    id=1,
    name="Test Camp",
    forest_name="Test Forest",
    pick_count=0,
    latitude=37.0,
    longitude=-119.0,
    source="fs_usda",
    reservation_type="reservation",
    num_sites=10,
    fee=None,
    fee_min=None,
    is_free=False,
    terrain=None,
    elevation_ft=5000,
    is_open=True,
    forecast_json=None,
    has_water=False,
    has_restrooms=False,
    toilet_type=None,
    water_feature=None,
    activities=None,
    is_reservable=False,
):
    """Build a campsite dict with the structure _row_to_dict produces."""
    return {
        "id": id,
        "name": name,
        "forest_name": forest_name,
        "latitude": latitude,
        "longitude": longitude,
        "source": source,
        "reservation_type": reservation_type,
        "num_sites": num_sites,
        "fee": fee,
        "fee_min": fee_min,
        "is_free": is_free,
        "terrain": terrain,
        "elevation_ft": elevation_ft,
        "pick_count": pick_count,
        "is_open": is_open,
        "forecast_json": forecast_json,
        "has_water": has_water,
        "has_restrooms": has_restrooms,
        "toilet_type": toilet_type,
        "water_feature": water_feature,
        "activities": activities or [],
        "is_reservable": is_reservable,
        "forecast": forecast_json,
    }


class TestUnknownAttrsOnNoQuery:
    """Verify unknown_attrs is present when no query is provided."""

    def test_no_query_all_rows_have_unknown_attrs(self, monkeypatch):
        """All rows returned in no-query path have unknown_attrs key."""
        fixtures = [
            _make_campsite(id=1, name="Camp A", forest_name="Forest A", has_water=None),
            _make_campsite(id=2, name="Camp B", forest_name="Forest B", has_water=True),
        ]
        monkeypatch.setattr("search._fetch_filtered", lambda filters: fixtures)

        results = search_campsites(query=None, water=True)

        assert len(results) == 2
        for row in results:
            assert "unknown_attrs" in row
            assert isinstance(row["unknown_attrs"], list)

    def test_no_query_marks_unknown_water(self, monkeypatch):
        """Row with NULL has_water is marked when water filter is active."""
        fixtures = [
            _make_campsite(id=1, has_water=None),
        ]
        monkeypatch.setattr("search._fetch_filtered", lambda filters: fixtures)

        results = search_campsites(query=None, water=True)

        assert len(results) == 1
        assert "has_water" in results[0]["unknown_attrs"]

    def test_no_query_no_unknown_when_no_filters(self, monkeypatch):
        """Row has empty unknown_attrs when no filters are applied."""
        fixtures = [_make_campsite(id=1)]
        monkeypatch.setattr("search._fetch_filtered", lambda filters: fixtures)

        results = search_campsites(query=None)

        assert len(results) == 1
        assert results[0]["unknown_attrs"] == []


class TestUnknownAttrsOnForestBranch:
    """Verify unknown_attrs is present in forest-substring results."""

    def test_forest_branch_all_rows_have_unknown_attrs(self, monkeypatch):
        """Forest-substring branch results have unknown_attrs."""
        fixtures = [
            _make_campsite(id=1, name="Camp A", forest_name="Stanislaus NF", has_water=None),
            _make_campsite(id=2, name="Camp B", forest_name="Stanislaus NF", has_water=True),
            _make_campsite(id=3, name="Stanislaus Lake", forest_name="Inyo"),
        ]
        monkeypatch.setattr("search._fetch_filtered", lambda filters: fixtures)

        results = search_campsites(query="stanislaus", water=True)

        # Should trigger forest branch (2 forest matches > 1 name match)
        for row in results:
            assert "unknown_attrs" in row
            assert isinstance(row["unknown_attrs"], list)

    def test_forest_branch_marks_unknown_attributes(self, monkeypatch):
        """Forest results mark unknown attributes correctly."""
        fixtures = [
            _make_campsite(id=1, name="Camp", forest_name="Stanislaus NF", has_water=None, is_free=None),
            _make_campsite(id=2, name="Camp", forest_name="Stanislaus NF", has_water=True, is_free=True),
        ]
        monkeypatch.setattr("search._fetch_filtered", lambda filters: fixtures)

        results = search_campsites(query="stanislaus", water=True, free_only=True)

        # First row should have unknowns, second should not
        assert "has_water" in results[0]["unknown_attrs"]
        assert "is_free" in results[0]["unknown_attrs"]
        assert results[1]["unknown_attrs"] == []


class TestUnknownAttrsOnScoredPath:
    """Verify unknown_attrs is present in scored (fuzzy match) results."""

    def test_scored_path_all_rows_have_unknown_attrs(self, monkeypatch):
        """Scored results have unknown_attrs."""
        fixtures = [
            _make_campsite(id=1, name="Pinecrest Camp", forest_name="Inyo", has_water=None),
            _make_campsite(id=2, name="Pineflat Camp", forest_name="Inyo", has_water=True),
        ]
        monkeypatch.setattr("search._fetch_filtered", lambda filters: fixtures)

        results = search_campsites(query="pinecrest", water=True)

        for row in results:
            assert "unknown_attrs" in row

    def test_scored_path_marks_unknown_attributes(self, monkeypatch):
        """Scored results mark unknown attributes."""
        fixtures = [
            _make_campsite(id=1, name="Pinecrest", forest_name="Inyo", has_water=None, is_reservable=None),
            _make_campsite(id=2, name="Pinecrest", forest_name="Inyo", has_water=True, is_reservable=False),
        ]
        monkeypatch.setattr("search._fetch_filtered", lambda filters: fixtures)

        results = search_campsites(query="pinecrest", water=True, reservable=True)

        # First result should have unknowns
        unknown_attrs = [r["unknown_attrs"] for r in results if r["id"] == 1]
        if unknown_attrs:
            assert "has_water" in unknown_attrs[0]
            assert "is_reservable" in unknown_attrs[0]


class TestMultipleFilters:
    """Test unknown_attrs with multiple filters."""

    def test_multiple_filters_all_tracked(self, monkeypatch):
        """Multiple filter requests track all unknowns."""
        fixtures = [
            _make_campsite(
                id=1,
                name="Test",
                forest_name="Test",
                has_water=None,
                is_open=None,
                is_reservable=True,
                is_free=False,
            ),
        ]
        monkeypatch.setattr("search._fetch_filtered", lambda filters: fixtures)

        results = search_campsites(query=None, water=True, is_open=True, reservable=True, free_only=True)

        assert len(results) == 1
        unknown = results[0]["unknown_attrs"]
        assert "has_water" in unknown
        assert "is_open" in unknown
        assert "is_reservable" not in unknown
        assert "is_free" not in unknown

    def test_no_false_unknowns(self, monkeypatch):
        """FALSE values are not marked as unknown."""
        fixtures = [
            _make_campsite(
                id=1,
                has_water=False,
                is_open=False,
                is_reservable=False,
            ),
        ]
        monkeypatch.setattr("search._fetch_filtered", lambda filters: fixtures)

        results = search_campsites(query=None, water=True, is_open=True, reservable=True)

        assert results[0]["unknown_attrs"] == []
