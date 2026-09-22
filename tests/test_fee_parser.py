"""Tests for the fee parser in scripts/clean_text.py.

Why this file exists: `fee` is the only field where the app shows a *derived*
number rather than the source's own words. fs.usda hands over several
paragraphs of pass-discount policy with the real price buried in the first
line, and `fee_min` drives the "under $X" search facet — so a parser slip
does not look like a bug, it looks like a campsite that quietly stops matching
a filter, or a wrong price on the page.

The raw strings below are the shapes seen in the wild, kept verbatim.
"""

import clean_text
from clean_text import parse_fee, parse_clean_fee, clean_prose


class TestParseFeeSimple:
    def test_bare_nightly_rate(self):
        display, fmin, fmax, free = parse_fee("$25 per night")
        assert display == "$25 / night"
        assert (fmin, fmax, free) == (25.0, 25.0, False)

    def test_slash_form_with_site(self):
        display, fmin, fmax, _ = parse_fee("$16 /site/night")
        assert display == "$16 / night"
        assert (fmin, fmax) == (16.0, 16.0)

    def test_decimal_amount_is_kept(self):
        display, fmin, _, _ = parse_fee("$12.50 per night")
        assert fmin == 12.5
        assert display == "$12.5 / night"

    def test_labelled_sites_render_in_canonical_order(self):
        raw = "Double Site: $50 per night. Single Site: $25 per night."
        display, fmin, fmax, _ = parse_fee(raw)
        # single leads regardless of the order the source listed them in
        assert display == "Single $25 · Double $50 / night"
        assert (fmin, fmax) == (25.0, 50.0)

    def test_additional_vehicle_fee_is_appended_not_averaged(self):
        raw = "Single Site: $20 per night. Additional vehicle fee: $8"
        display, fmin, fmax, _ = parse_fee(raw)
        assert display.endswith("+$8/vehicle")
        # the vehicle fee must not drag fee_min/fee_max around: those are the
        # per-site price the "under $X" facet filters on.
        assert (fmin, fmax) == (20.0, 20.0)

    def test_range_when_unlabelled_amounts_differ(self):
        display, fmin, fmax, _ = parse_fee("$10 per night to $30 per night")
        assert display == "$10–$30 / night"
        assert (fmin, fmax) == (10.0, 30.0)


class TestParseFeeFree:
    def test_plain_free(self):
        assert parse_fee("Free") == ("Free", 0.0, 0.0, True)

    def test_no_fee_prose(self):
        display, fmin, fmax, free = parse_fee("There is no fee to camp here.")
        assert free is True
        assert (display, fmin, fmax) == ("Free", 0.0, 0.0)

    def test_bare_no_is_free(self):
        # fs.usda answers the "Fee?" field with a literal "No."
        assert parse_fee("No.")[3] is True

    def test_free_wording_with_a_price_present_is_not_free(self):
        # "no fee for day use" but camping still costs money -- the presence of
        # a dollar amount has to win, or a paid site lands in the free facet.
        display, fmin, _, free = parse_fee("No fee for day use. Camping is $20 per night.")
        assert free is False
        assert fmin == 20.0


class TestParseFeePolicyProse:
    """The head/marker split: stop reading once the text turns into policy."""

    def test_interagency_pass_discount_does_not_become_the_price(self):
        raw = (
            "Single Site: $26 per night\n"
            "The Interagency Senior and Access passes are honored for a 50 percent "
            "discount, so a $13 rate applies to pass holders."
        )
        display, fmin, fmax, _ = parse_fee(raw)
        assert (fmin, fmax) == (26.0, 26.0)
        assert "13" not in display

    def test_concessionaire_prose_is_cut(self):
        raw = (
            "$22 per night. This campground is operated by a concessionaire and "
            "a $9 reservation charge is added at the time of booking."
        )
        _, fmin, fmax, _ = parse_fee(raw)
        assert (fmin, fmax) == (22.0, 22.0)

    def test_loose_dollar_figure_is_the_last_resort(self):
        # no "per night" anywhere, but there is a price: better than nothing
        _, fmin, _, _ = parse_fee("Camping fee $14.")
        assert fmin == 14.0


class TestParseFeeUnparseable:
    def test_none_in_none_out(self):
        assert parse_fee(None) == (None, None, None, None)

    def test_empty_string(self):
        assert parse_fee("   ") == (None, None, None, None)

    def test_prose_with_no_amount_yields_no_display(self):
        display, fmin, fmax, free = parse_fee("Fees vary by season; call the ranger district.")
        assert display is None
        assert (fmin, fmax) == (None, None)
        assert free is False

    def test_nbsp_is_not_treated_as_content(self):
        assert parse_fee("\xa0\xa0")[0] is None


class TestParseFeeIdempotent:
    """clean_text always re-parses from fee_raw, so running it twice must not
    drift -- and its own output must survive a second pass unchanged."""

    def test_reparsing_own_output_is_stable(self):
        first, fmin, fmax, _ = parse_fee("Single Site: $25 per night")
        assert first == "Single $25 / night"
        assert (fmin, fmax) == (25.0, 25.0)
        # feeding the display string back in must find the same price
        assert parse_fee(first)[1] == 25.0


class TestParseCleanFee:
    """The Dyrt hands over already-tidy strings; this only pulls min/max out."""

    def test_single_amount(self):
        assert parse_clean_fee("$5") == ("$5", 5.0, 5.0, False)

    def test_en_dash_range(self):
        display, lo, hi, free = parse_clean_fee("$75–$180")
        assert (lo, hi) == (75.0, 180.0)
        assert display == "$75–$180"

    def test_hyphen_range(self):
        _, lo, hi, _ = parse_clean_fee("$20-$40")
        assert (lo, hi) == (20.0, 40.0)

    def test_free_string(self):
        assert parse_clean_fee("Free") == ("Free", 0.0, 0.0, True)

    def test_zero_dollars_is_free(self):
        assert parse_clean_fee("$0")[3] is True

    def test_unparseable_keeps_the_original_string(self):
        display, lo, hi, _ = parse_clean_fee("varies")
        assert display == "varies"
        assert (lo, hi) == (None, None)

    def test_none(self):
        assert parse_clean_fee(None) == (None, None, None, None)


class TestCleanProse:
    def test_strips_leading_overview_label(self):
        assert clean_prose("OverviewSawtooth Canyon is popular.") == "Sawtooth Canyon is popular."

    def test_strips_label_with_colon(self):
        assert clean_prose("Description: A quiet site.") == "A quiet site."

    def test_repairs_missing_space_after_full_stop(self):
        # the run-on bug that hit 427 rows
        assert clean_prose("Thank you!Tuolumne Meadows is open.") == (
            "Thank you! Tuolumne Meadows is open."
        )

    def test_glued_section_header_gets_a_paragraph_break(self):
        out = clean_prose("best trout fishing.Recreation\nScenic hiking abounds.")
        assert "\n\nRecreation\n" in out

    def test_hard_wrapped_line_is_joined(self):
        assert clean_prose("water skiing and\nfishing") == "water skiing and fishing"

    def test_nbsp_becomes_a_space(self):
        assert clean_prose("a\xa0b") == "a b"

    def test_blank_input_is_none(self):
        assert clean_prose("") is None
        assert clean_prose(None) is None
        assert clean_prose("   \n  ") is None

    def test_sentence_splitter_leaves_decimals_alone(self):
        # "$12.50" must not become "$12. 50"
        assert clean_prose("The fee is $12.50 nightly.") == "The fee is $12.50 nightly."


def test_parse_fee_is_importable_by_the_scrapers():
    """The module docstring promises these are importable helpers -- the
    scrapers call them at write time, not just the batch job."""
    assert callable(clean_text.parse_fee)
    assert callable(clean_text.clean_prose)
