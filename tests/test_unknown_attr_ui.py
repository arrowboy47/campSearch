"""Tests for the unknown attributes UI rendering (partial template).

Tests that unknown_attrs are correctly rendered as chips in the UI,
and that cost-related attributes are deduplicated.
"""

import pytest
from jinja2 import Environment, FileSystemLoader
import os


@pytest.fixture
def jinja_env():
    """Create a Jinja environment pointing to the templates directory."""
    template_dir = os.path.join(
        os.path.dirname(__file__), "..", "templates"
    )
    return Environment(loader=FileSystemLoader(template_dir))


def test_no_chips_when_unknown_attrs_empty(jinja_env):
    """A row with unknown_attrs: [] renders NO chip."""
    template = jinja_env.get_template("_unknown_attrs.html")
    result = template.render(unknown_attrs=[])

    # Should be empty or whitespace only (no unknown-attr-chip elements)
    assert "unknown-attr-chip" not in result
    assert "unknown unknown" not in result


def test_single_cost_chip_for_is_free(jinja_env):
    """A row with is_free in unknown_attrs renders exactly ONE 'cost unknown' chip."""
    template = jinja_env.get_template("_unknown_attrs.html")
    result = template.render(unknown_attrs=["is_free"])

    # Should have one chip
    assert result.count("unknown-attr-chip") == 1
    assert "cost unknown" in result


def test_single_cost_chip_for_fee_min(jinja_env):
    """A row with fee_min in unknown_attrs renders exactly ONE 'cost unknown' chip."""
    template = jinja_env.get_template("_unknown_attrs.html")
    result = template.render(unknown_attrs=["fee_min"])

    # Should have one chip
    assert result.count("unknown-attr-chip") == 1
    assert "cost unknown" in result


def test_single_cost_chip_for_both_cost_keys(jinja_env):
    """A row with both is_free AND fee_min renders exactly ONE 'cost unknown' chip, not two."""
    template = jinja_env.get_template("_unknown_attrs.html")
    result = template.render(unknown_attrs=["is_free", "fee_min"])

    # Should have exactly one chip, not two
    assert result.count("unknown-attr-chip") == 1
    assert result.count("cost unknown") == 1


def test_multiple_distinct_unknown_attrs(jinja_env):
    """A row with several distinct unknown attributes renders one chip each."""
    template = jinja_env.get_template("_unknown_attrs.html")
    result = template.render(
        unknown_attrs=["is_free", "has_water", "toilet_type", "is_reservable"]
    )

    # Should have four chips (one for each distinct attribute)
    assert result.count("unknown-attr-chip") == 4
    assert "cost unknown" in result
    assert "water unknown" in result
    assert "toilets unknown" in result
    assert "reservations unknown" in result


def test_all_possible_unknown_attrs(jinja_env):
    """Test all possible unknown attribute keys render correctly."""
    template = jinja_env.get_template("_unknown_attrs.html")
    result = template.render(
        unknown_attrs=["is_open", "has_water", "toilet_type", "is_free", "fee_min", "is_reservable"]
    )

    # Should have 5 chips (cost is deduplicated)
    assert result.count("unknown-attr-chip") == 5
    assert "open status unknown" in result
    assert "water unknown" in result
    assert "toilets unknown" in result
    assert "cost unknown" in result
    assert "reservations unknown" in result


def test_unknown_attrs_row_container_missing_when_empty(jinja_env):
    """When unknown_attrs is empty, the container div should not render."""
    template = jinja_env.get_template("_unknown_attrs.html")
    result = template.render(unknown_attrs=[])

    # No container should be rendered
    assert "unknown-attrs-row" not in result


def test_unknown_attrs_row_container_present_when_not_empty(jinja_env):
    """When unknown_attrs is not empty, the container div should render."""
    template = jinja_env.get_template("_unknown_attrs.html")
    result = template.render(unknown_attrs=["has_water"])

    # Container should be present
    assert "unknown-attrs-row" in result


def test_deduplication_preserves_order(jinja_env):
    """Unknown attrs are shown in a consistent order regardless of input order."""
    template = jinja_env.get_template("_unknown_attrs.html")

    # Try two different input orders
    result1 = template.render(unknown_attrs=["is_reservable", "has_water", "is_free"])
    result2 = template.render(unknown_attrs=["has_water", "is_free", "is_reservable"])

    # Extract the chip order from both results
    import re
    chips1 = re.findall(r'>([^<]+) unknown<', result1)
    chips2 = re.findall(r'>([^<]+) unknown<', result2)

    # Both should have the same chips (deduplication works)
    assert set(chips1) == set(chips2)


class TestResultsPageIntegration:
    """The chip must render from inside the real results.html loop.

    The partial reads a bare `unknown_attrs`, but inside
    `{% for camp in campsites %}` the data lives on `camp.unknown_attrs`.
    A plain `{% include %}` inherits context without rebinding that name, so
    the partial silently renders nothing. A test that exercises the partial in
    isolation cannot catch this, and did not: the bug shipped into the working
    tree once already. These tests read templates/results.html itself.
    """

    def _results_html(self):
        import os
        p = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "templates", "results.html")
        with open(p, encoding="utf-8") as fh:
            return fh.read()

    def test_include_rebinds_unknown_attrs_from_the_loop_variable(self):
        """A bare include here renders nothing. The binding must be present."""
        html = self._results_html()
        assert '{% include "_unknown_attrs.html" %}' in html, (
            "results.html no longer includes the partial")
        # The include must be preceded by a with-binding on the same line.
        for line in html.splitlines():
            if '{% include "_unknown_attrs.html" %}' in line:
                assert "unknown_attrs = camp.unknown_attrs" in line, (
                    "the include does not rebind unknown_attrs from camp, so "
                    "the partial will silently render no chips: " + line.strip())
                break

    def test_results_template_renders_chips_for_unknown_rows(self):
        """Render the real loop fragment lifted out of results.html."""
        from jinja2 import Environment, FileSystemLoader
        import os, re
        tpl_dir = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "templates")
        html = self._results_html()
        line = [l for l in html.splitlines()
                if '{% include "_unknown_attrs.html" %}' in l][0]
        env = Environment(loader=FileSystemLoader(tpl_dir))
        frag = env.from_string(
            "{% for camp in campsites %}" + line.strip() + "{% endfor %}")
        out = frag.render(campsites=[
            {"id": 1, "unknown_attrs": ["is_free"]},
            {"id": 2, "unknown_attrs": []},
            {"id": 3, "unknown_attrs": ["is_free", "fee_min"]},
        ])
        assert out.count("unknown-attr-chip") == 2, (
            "expected one chip for each of the two unknown rows, cost "
            "collapsing to a single chip on row 3")
        assert "cost unknown" in out

    def test_hint_line_is_conditional_on_there_being_unknowns(self):
        html = self._results_html()
        assert "results-unknown-hint" in html, "the summary hint line is gone"
        idx = html.index("results-unknown-hint")
        preceding = html[:idx]
        assert "{% if unknown_count %}" in preceding, (
            "the hint line must be guarded so it is absent when every row is "
            "confirmed")

    def test_results_template_has_no_stray_edit_artifacts(self):
        """A contractor killed mid-edit once left a literal `111c` behind."""
        import re
        for n, line in enumerate(self._results_html().splitlines(), 1):
            assert not re.match(r"^\d+[acd]$", line.strip()), (
                "ed artifact at results.html:%d: %r" % (n, line))
