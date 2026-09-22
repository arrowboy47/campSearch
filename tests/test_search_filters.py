"""Tests for the dynamic WHERE builder and the name scorer in search.py.

Why this file exists: `_build_where` decides which campsites a facet can ever
return, and it fails silently. A dropped clause does not raise -- it just widens
the result set, and a clause that binds its parameter in the wrong order gives
the *wrong* campsites with no error at all. Both are invisible without a test
that reads the generated SQL.

These are pure-function tests: no database, no connection. The fragment is
checked as text and the params as a list, which is exactly the pair handed to
psycopg2.
"""

import math

import pytest

from search import (
    _build_where,
    _land_type,
    _name_score,
    _popularity_bonus,
    _row_to_dict,
    normalize,
)


def where(**filters):
    return _build_where(filters)


class TestNoFilters:
    def test_empty_filters_add_nothing(self):
        frag, params = where()
        assert frag == ""
        assert params == []

    def test_none_and_falsey_values_are_skipped(self):
        frag, params = where(
            forest=None, water=False, terrain=[], activities=[], fee_max=None,
            elev_min=None, is_open=False,
        )
        assert frag == ""
        assert params == []

    def test_every_clause_is_anded_on(self):
        frag, _ = where(water=True, is_open=True)
        assert frag.startswith(" AND ")
        assert frag.count(" AND ") == 2


class TestBooleanFacets:
    def test_is_open(self):
        frag, params = where(is_open=True)
        assert "su.is_open = TRUE" in frag
        assert params == []

    def test_water(self):
        frag, _ = where(water=True)
        assert "am.water = TRUE" in frag

    def test_reservable(self):
        frag, _ = where(reservable=True)
        assert "r.is_reservable = TRUE" in frag


class TestForest:
    def test_forest_is_a_bound_parameter_not_interpolated(self):
        frag, params = where(forest="Stanislaus")
        assert "LOWER(c.forest_name) LIKE %s" in frag
        assert params == ["%stanislaus%"]

    def test_forest_is_lowercased_and_stripped(self):
        _, params = where(forest="  Inyo National Forest \n")
        assert params == ["%inyo national forest%"]

    def test_quote_in_forest_name_stays_in_the_parameter(self):
        # a stray quote must ride in the param, never into the SQL text
        frag, params = where(forest="O'Neill")
        assert "O'Neill" not in frag
        assert params == ["%o'neill%"]


class TestToilet:
    @pytest.mark.parametrize("kind", ["flush", "vault"])
    def test_specific_type_binds_the_value(self, kind):
        frag, params = where(toilet=kind)
        assert "am.toilet_type = %s" in frag
        assert params == [kind]

    def test_any_excludes_none_as_well_as_null(self):
        # 'none' is a real stored value meaning "no toilet", so IS NOT NULL
        # alone would hand back sites that have no toilet at all.
        frag, params = where(toilet="any")
        assert "am.toilet_type IS NOT NULL" in frag
        assert "<> 'none'" in frag
        assert params == []

    def test_unknown_toilet_value_adds_no_clause(self):
        frag, params = where(toilet="composting")
        assert frag == ""
        assert params == []


class TestFee:
    def test_free_only(self):
        frag, params = where(free_only=True)
        assert "c.is_free = TRUE" in frag
        assert params == []

    def test_fee_max_keeps_free_sites_in_range(self):
        # a free site has fee_min NULL in places, and "under $20" plainly
        # includes $0 -- dropping the is_free arm hides every free campsite
        # from a price-capped search.
        frag, params = where(fee_max=20)
        assert "c.is_free = TRUE OR c.fee_min <= %s" in frag
        assert params == [20]

    def test_free_only_wins_over_fee_max(self):
        frag, params = where(free_only=True, fee_max=20)
        assert "fee_min" not in frag
        assert params == []

    def test_fee_max_of_zero_is_still_applied(self):
        # 0 is falsey; the builder has to test `is not None` or a "$0 max"
        # search silently becomes an unfiltered one
        frag, params = where(fee_max=0)
        assert "c.fee_min <= %s" in frag
        assert params == [0]


class TestCampingType:
    def test_dispersed(self):
        frag, _ = where(camping_type="dispersed")
        assert "c.reservation_type = 'dispersed'" in frag

    def test_developed_uses_is_distinct_from_so_nulls_are_included(self):
        # ~1000 rows had a NULL reservation_type before migration 0015 and new
        # sources can reintroduce them; `<> 'dispersed'` would drop every one.
        frag, _ = where(camping_type="developed")
        assert "IS DISTINCT FROM 'dispersed'" in frag

    def test_unknown_camping_type_adds_no_clause(self):
        assert where(camping_type="glamping") == ("", [])


class TestElevation:
    def test_min_and_max_bind_in_order(self):
        frag, params = where(elev_min=4000, elev_max=9000)
        assert frag.index("c.elevation_ft >= %s") < frag.index("c.elevation_ft <= %s")
        assert params == [4000, 9000]

    def test_zero_elevation_min_is_applied(self):
        # sea-level sites exist; 0 must not be read as "unset"
        _, params = where(elev_min=0)
        assert params == [0]


class TestArrayFacets:
    def test_terrain_is_any_of(self):
        frag, params = where(terrain=["alpine", "valley"])
        assert "c.terrain = ANY(%s)" in frag
        assert params == [["alpine", "valley"]]

    def test_water_feature_is_any_of(self):
        frag, params = where(water_feature=["lake"])
        assert "am.water_feature = ANY(%s)" in frag
        assert params == [["lake"]]

    def test_activities_uses_overlap_not_containment(self):
        # && is "shares at least one"; @> would demand the site offer *all*
        # of them, which is not what the UI says the filter does.
        frag, params = where(activities=["hiking", "fishing"])
        assert "am.activities && %s" in frag
        assert params == [["hiking", "fishing"]]

    def test_array_params_are_lists_psycopg2_can_adapt(self):
        _, params = where(terrain=("alpine",), activities={"hiking"})
        assert all(isinstance(p, list) for p in params)


class TestParamOrdering:
    def test_params_line_up_with_placeholders(self):
        frag, params = where(
            forest="Inyo", toilet="flush", fee_max=25,
            elev_min=1000, elev_max=8000, terrain=["valley"],
        )
        assert frag.count("%s") == len(params)

    def test_order_is_positional_not_alphabetical(self):
        frag, params = where(forest="Inyo", toilet="vault", fee_max=15)
        # forest is built before toilet, which is built before fee
        assert params == ["%inyo%", "vault", 15]


class TestNameScore:
    def test_exact_match_is_100(self):
        assert _name_score(normalize("Pinecrest"), normalize("pinecrest")) == 100.0

    def test_leading_substring_beats_a_buried_one(self):
        lead = _name_score(normalize("Pinecrest Campground"), "pinecrest")
        buried = _name_score(normalize("Lake Pinecrest Camp"), "pinecrest")
        assert lead > buried

    def test_any_substring_hit_clears_the_default_threshold(self):
        # the "pinecrest" regression: a real substring match must never be
        # scored below the 62 cut-off and vanish from the results
        assert _name_score(normalize("Upper Pinecrest Group Campground"), "pinecrest") >= 80.0

    def test_substring_beats_fuzzy(self):
        real = _name_score(normalize("Pinecrest Campground"), "pinecrest")
        fuzzy = _name_score(normalize("Pine Flat Campground"), "pinecrest")
        assert real > fuzzy

    def test_empty_name_scores_zero(self):
        assert _name_score("", "pinecrest") == 0.0

    def test_whitespace_insensitive_via_normalize(self):
        # the documented reason normalize() strips all whitespace
        assert _name_score(normalize("Pinecrest"), normalize("pine crest")) == 100.0


class TestNormalize:
    def test_none_is_empty_string(self):
        assert normalize(None) == ""

    def test_case_and_all_whitespace_removed(self):
        assert normalize("  Lake   TAHOE \n") == "laketahoe"


class TestPopularityBonus:
    def test_no_picks_is_no_bonus(self):
        assert _popularity_bonus(0) == 0.0
        assert _popularity_bonus(None) == 0.0

    def test_is_capped(self):
        assert _popularity_bonus(10_000_000) == 8.0

    def test_cap_stays_below_the_substring_floor(self):
        # a popular fuzzy match must not be able to climb past a real
        # substring hit, which floors at 80
        assert _popularity_bonus(10_000_000) < 80.0

    def test_grows_with_picks_but_sublinearly(self):
        assert _popularity_bonus(10) > _popularity_bonus(1)
        assert _popularity_bonus(100) < 10 * _popularity_bonus(1)


class TestRowToDict:
    def _row(self, **over):
        vals = {
            "id": 1, "name": "Test", "forest_name": "Inyo", "latitude": 37.5,
            "longitude": -119.0, "source": "fs_usda", "reservation_type": "reservation",
            "num_sites": 10, "fee": "$20 / night", "fee_min": 20, "is_free": False,
            "terrain": "valley", "elevation_ft": 5000, "pick_count": 3,
            "is_open": True, "forecast_json": {"t": 1}, "has_water": True,
            "has_restrooms": None, "toilet_type": "vault", "water_feature": "lake",
            "activities": None, "is_reservable": True,
        }
        vals.update(over)
        from search import _ROW_FIELDS
        return tuple(vals[f] for f in _ROW_FIELDS)

    def test_nan_coords_become_none(self):
        # campsite 845's NaN latitude produced invalid JSON and blanked the
        # whole map -- it must never reach the template again
        d = _row_to_dict(self._row(latitude=float("nan")))
        assert d["latitude"] is None

    def test_real_coords_survive_as_floats(self):
        d = _row_to_dict(self._row())
        assert d["latitude"] == pytest.approx(37.5)
        assert isinstance(d["longitude"], float)

    def test_null_amenity_flags_become_false_not_none(self):
        d = _row_to_dict(self._row(has_restrooms=None))
        assert d["has_restrooms"] is False

    def test_null_activities_becomes_an_empty_list(self):
        assert _row_to_dict(self._row(activities=None))["activities"] == []

    def test_forecast_key_is_aliased_for_the_template(self):
        d = _row_to_dict(self._row())
        assert d["forecast"] == {"t": 1}


class TestLandType:
    def test_known_agencies_map_to_legend_buckets(self):
        assert _land_type("US Forest Service") == "national_forest"
        assert _land_type("California State Parks") == "state_park"
        assert _land_type("Bureau of Land Management") == "blm"

    def test_unknown_and_missing_agency_fall_back_to_other(self):
        # 189 dispersed rows have no agency at all
        assert _land_type(None) == "other"
        assert _land_type("Some New Agency") == "other"
