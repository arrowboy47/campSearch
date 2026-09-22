"""Tests for the per-source parsing helpers.

Why this file exists: these functions turn scraped HTML and undocumented JSON
into the values the facets filter on. When a source changes shape the scraper
keeps running and keeps writing rows -- it just writes worse ones. Nothing here
touches the network or the database; these are the pure halves of each job,
pinned to the inputs the sources actually send.
"""

import pytest

from _pipeline import is_map_image
from backfill_elevation import terrain_band
from derive_attributes import derive
from geocode_coordless import candidates, in_ca
from scrape_fs_usda import (
    forest_slug,
    infer_amenities,
    managing_unit_from_slug,
    parse_open,
)
from scrape_thedyrt import fee_text, region_slug


class TestFsUsdaSlugs:
    def test_forest_slug_takes_the_last_segment(self):
        assert forest_slug("r05/shasta-trinity") == "shasta-trinity"

    def test_trailing_slash_is_ignored(self):
        assert forest_slug("r05/inyo/") == "inyo"

    def test_managing_unit_is_title_cased_with_spaces(self):
        assert managing_unit_from_slug("shasta-trinity") == "Shasta Trinity National Forest"


class TestParseOpen:
    def test_open_text(self):
        assert parse_open("Open") is True

    def test_closed_text(self):
        assert parse_open("Closed for the season") is False

    def test_closed_wins_when_both_words_appear(self):
        # "Open May-Sep, closed in winter" is a closed site today; guessing
        # True here would put it in the is_open facet wrongly.
        assert parse_open("Open May through September, closed in winter") is False

    def test_unknown_stays_none_not_false(self):
        # None means "no information"; False means "we know it is shut".
        # Collapsing them would mark every unscraped site as closed.
        assert parse_open("Seasonal") is None
        assert parse_open("") is None
        assert parse_open(None) is None


class TestInferAmenities:
    def test_potable_water_is_yes(self):
        water, _ = infer_amenities("Potable water is available at the host site.")
        assert water is True

    def test_no_water_is_no(self):
        water, _ = infer_amenities("There is no potable water at this campground.")
        assert water is False

    def test_non_potable_is_no(self):
        assert infer_amenities("Non-potable water only.")[0] is False

    def test_toilets_detected(self):
        _, restrooms = infer_amenities("Vault toilets are provided.")
        assert restrooms is True

    def test_no_restrooms_is_no(self):
        assert infer_amenities("No restrooms.")[1] is False

    def test_silence_is_unknown_not_absent(self):
        assert infer_amenities("A quiet spot by the meadow.") == (None, None)

    def test_empty_blurb(self):
        assert infer_amenities("") == (None, None)
        assert infer_amenities(None) == (None, None)


class TestTheDyrtFeeText:
    def test_zero_low_is_free(self):
        assert fee_text(0, 0) == "Free"
        assert fee_text(None, None) == "Free"

    def test_single_amount_from_cents(self):
        assert fee_text(500, 500) == "$5"

    def test_range_uses_an_en_dash(self):
        assert fee_text(7500, 18000) == "$75–$180"

    def test_missing_high_falls_back_to_low(self):
        assert fee_text(2000, None) == "$20"

    def test_output_is_reparseable_by_clean_text(self):
        # the two halves have to agree: parse_clean_fee reads exactly what
        # this writes, so a format change here silently blanks fee_min there
        from clean_text import parse_clean_fee

        _, lo, hi, _ = parse_clean_fee(fee_text(7500, 18000))
        assert (lo, hi) == (75.0, 180.0)


class TestRegionSlug:
    def test_spaces_become_hyphens_and_lowercase(self):
        assert region_slug("California") == "california"
        assert region_slug("New Mexico") == "new-mexico"

    def test_blank_is_unknown_not_empty(self):
        assert region_slug("") == "unknown"
        assert region_slug(None) == "unknown"
        assert region_slug("   ") == "unknown"


class TestTerrainBand:
    def test_elevation_bands(self):
        # a mid-Sierra longitude/latitude so the desert and coastal boxes
        # do not claim these
        lat, lon = 38.0, -120.0
        assert terrain_band(10_000, lat, lon) == "alpine"
        assert terrain_band(8_000, lat, lon) == "subalpine forest"
        assert terrain_band(5_000, lat, lon) == "montane forest"
        assert terrain_band(2_000, lat, lon) == "foothills"
        assert terrain_band(500, lat, lon) == "valley"

    def test_band_boundaries_are_inclusive_at_the_bottom(self):
        lat, lon = 38.0, -120.0
        assert terrain_band(9_500, lat, lon) == "alpine"
        assert terrain_band(9_499, lat, lon) == "subalpine forest"

    def test_southeast_desert_box(self):
        # Joshua Tree-ish: low, south, east
        assert terrain_band(3_000, 34.0, -116.0) == "high desert"

    def test_high_southeast_peaks_are_not_desert(self):
        # the box is capped at 4500ft so San Bernardino peaks stay forest
        assert terrain_band(6_000, 34.0, -116.0) == "montane forest"

    def test_coastal_box(self):
        assert terrain_band(200, 39.0, -123.5) == "coastal"

    def test_inland_low_elevation_is_not_coastal(self):
        assert terrain_band(200, 38.0, -120.0) == "valley"

    def test_missing_elevation_stays_null_for_a_later_run(self):
        assert terrain_band(None, 38.0, -120.0) is None

    def test_string_coords_are_accepted(self):
        # psycopg2 hands back Decimal/str for numeric columns
        assert terrain_band(10_000, "38.0", "-120.0") == "alpine"


class TestDeriveAttributes:
    def test_activities_are_sorted_and_deduped(self):
        acts, _, _ = derive("Hiking Camp", "hiking, fishing", "Great hiking here.", None, None)
        assert acts == sorted(set(acts))
        assert "hiking" in acts

    def test_mountain_biking_collapses_plain_biking(self):
        acts, _, _ = derive(None, "mountain biking and biking", None, None, None)
        assert "mountain biking" in acts
        assert "biking" not in acts

    def test_swimming_beach_expands_to_both_tags(self):
        acts, _, _ = derive(None, "swimming beach", None, None, None)
        assert "swimming beach" not in acts
        assert {"swimming", "beach access"} <= set(acts)

    def test_water_feature_first_rule_wins(self):
        _, water, _ = derive(None, None, "On the shore of a lake fed by a creek.", None, None)
        assert water == "lake"

    def test_body_of_water_column_is_the_fallback(self):
        _, water, _ = derive(None, None, "A quiet spot.", "Unnamed reservoir", None)
        assert water == "water nearby"

    def test_no_water_signal_stays_none(self):
        assert derive(None, None, "A dry camp.", None, None)[1] is None

    def test_toilet_type_from_text(self):
        assert derive(None, "vault toilet", None, None, None)[2] == "vault"
        assert derive(None, "flush toilets", None, None, None)[2] == "flush"

    def test_explicit_no_toilets(self):
        assert derive(None, None, "No toilets are provided.", None, None)[2] == "none"

    def test_restrooms_flag_is_the_fallback(self):
        # a structured True from another source, with no prose to be specific
        assert derive(None, None, "A quiet spot.", None, True)[2] == "restrooms"

    def test_all_empty_input(self):
        assert derive(None, None, None, None, None) == ([], None, None)


class TestGeocodeCandidates:
    ADDR = "12 Forest Route 4, Big Bear County, CA 92315"

    def test_county_anchored_name_comes_first(self):
        out = candidates("Pine Flat Campground", self.ADDR)
        assert out[0] == "Pine Flat, Big Bear County, CA"

    def test_noise_words_are_stripped_from_the_name(self):
        out = candidates("Lower Group Campsites Area", self.ADDR)
        assert "Campsites" not in out[0]
        assert "Big Bear County, CA" in out[0]

    def test_street_candidate_is_offered_when_present(self):
        out = candidates("Pine Flat Campground", self.ADDR)
        assert any(c.startswith("12 Forest Route 4,") for c in out)

    def test_full_address_is_always_the_last_resort(self):
        assert candidates("Pine Flat Campground", self.ADDR)[-1] == self.ADDR

    def test_candidates_are_unique_and_ordered(self):
        out = candidates("Pine Flat Campground", self.ADDR)
        assert len(out) == len(set(out))

    def test_no_county_means_no_anchored_guessing(self):
        # a bare name geocodes to the wrong same-named place too often, so
        # without a county the only candidate is the address itself
        assert candidates("Pine Flat Campground", "somewhere vague") == ["somewhere vague"]

    def test_no_address_at_all_yields_nothing(self):
        assert candidates("Pine Flat Campground", None) == []
        assert candidates("Pine Flat Campground", "") == []


class TestInCa:
    """A rectangle, not the state outline.

    It exists to throw out a geocode that landed in another region entirely --
    Nominatim will happily return the same-named place several states over.
    Because California is diagonal, the rectangle necessarily also admits
    western Nevada; those are caught (if at all) by the county anchoring in
    `candidates`, not here. The tests say what the guard actually promises.
    """

    def test_a_sierra_point_is_inside(self):
        assert in_ca(37.8, -119.5) is True

    def test_southern_and_northern_extremes_are_inside(self):
        assert in_ca(32.7, -117.1) is True   # San Diego
        assert in_ca(41.8, -124.2) is True   # Crescent City

    def test_oregon_is_outside(self):
        assert in_ca(44.0, -120.5) is False

    def test_utah_is_outside(self):
        assert in_ca(38.5, -111.0) is False

    def test_the_open_pacific_is_outside(self):
        assert in_ca(37.0, -127.0) is False

    def test_a_wholly_different_state_is_outside(self):
        # the failure mode this guard is really for
        assert in_ca(35.2, -80.8) is False   # North Carolina

    def test_western_nevada_is_a_known_false_positive(self):
        # documented, not desired: a bounding box cannot exclude it. If this
        # ever starts failing, the guard grew a real polygon and this test
        # should be deleted rather than "fixed".
        assert in_ca(39.5, -114.5) is True


class TestIsMapImage:
    @pytest.mark.parametrize(
        "title",
        ["Campground Map", "Site Plan", "Vicinity Map", "camping area diagram", "LAYOUT"],
    )
    def test_map_titles_are_rejected(self, title):
        assert is_map_image(title) is True

    @pytest.mark.parametrize(
        "title",
        ["Lakeside view", "Campsite 14", "Sunset over the meadow", ""],
    )
    def test_photo_titles_are_kept(self, title):
        assert is_map_image(title) is False

    def test_none_title_is_kept(self):
        assert is_map_image(None) is False

    def test_substring_of_a_longer_word_does_not_match(self):
        # "Mapleton" must not be read as a map
        assert is_map_image("Mapleton Ridge") is False
