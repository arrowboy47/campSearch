"""Tests for impression and click tracking on results page.

Tests cover:
- Template rendering of position attributes and search data
- Endpoint contract for impression and click events
- JavaScript guarding against double-sending
- Use of Blob with application/json type for sendBeacon
"""

import pytest
import json
from jinja2 import Environment, FileSystemLoader
import os
from unittest.mock import patch

import app as app_module


def _render_results(**context):
    """Render templates/results.html through the Flask app's own Jinja env.

    The template calls url_for, which only resolves inside an application
    context, so a bare jinja2 Environment raises UndefinedError. Going through
    the app keeps these tests pointed at the real file rather than a fragment,
    which is the point: a fragment test cannot catch a mistake made in the
    template itself.

    The request context is entered and exited around the render. Pushing one
    and leaving it on the stack leaks context between tests, which is the kind
    of thing that makes an unrelated test fail later for no visible reason.
    """
    import app as app_module
    with app_module.app.test_request_context("/results"):
        tpl = app_module.app.jinja_env.get_template("results.html")
        return tpl.render(**context)


import db as db_module


class TestResultsTemplateTracking:
    """Tests for impression tracking data attributes in results.html."""

    def _results_html(self):
        """Read the actual results.html file."""
        p = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "templates", "results.html")
        with open(p, encoding="utf-8") as fh:
            return fh.read()

    def test_search_data_json_block_present(self):
        """results.html must include a searchData JSON block."""
        html = self._results_html()
        assert 'id="searchData"' in html, "searchData JSON block is missing"
        assert 'type="application/json"' in html and 'searchData' in html

    def test_result_cards_have_position_attribute(self):
        """Each result card must have data-position with loop.index."""
        html = self._results_html()
        assert 'data-position="{{ loop.index }}"' in html, (
            "result card is missing data-position with loop.index")

    def test_result_cards_have_campsite_id_attribute(self):
        """Each result card must have data-campsite-id."""
        html = self._results_html()
        assert 'data-campsite-id="{{ camp.id }}"' in html, (
            "result card is missing data-campsite-id")

    def test_positions_are_one_based_not_zero_based(self):
        """Positions must use loop.index (1-based), not loop.index0 (0-based)."""
        html = self._results_html()
        # Verify we use loop.index, not loop.index0
        for line in html.splitlines():
            if 'data-position=' in line:
                assert 'loop.index0' not in line, (
                    "data-position must not use loop.index0 (0-based); "
                    "use loop.index for 1-based positions: " + line.strip())

    def test_search_data_renders_with_query(self):
        """searchData JSON block renders the query text."""
        tpl_dir = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "templates")
        result = _render_results(
            query="tent camping",
            campsites=[
                {"id": 1, "name": "Camp A", "latitude": 35.5, "longitude": -119.5,
                 "unknown_attrs": [], "is_open": True, "forest_name": None,
                 "is_free": False, "fee": "$20", "reservation_type": None,
                 "has_water": False, "toilet_type": None, "water_feature": None,
                 "terrain": None, "elevation_ft": None, "activities": [],
                 "weather_summary": None},
            ],
            start_date=None,
            end_date=None,
            forests=[],
            result_count=1,
        )

        # Parse the searchData JSON
        import re
        match = re.search(r'id="searchData">([^<]+)<', result)
        assert match, "Could not find searchData JSON in output"
        data = json.loads(match.group(1))
        assert data.get("query") == "tent camping"

    def test_search_data_empty_when_no_query(self):
        """searchData is safe when query is empty."""
        tpl_dir = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "templates")
        result = _render_results(
            query="",
            campsites=[
                {"id": 1, "name": "Camp A", "latitude": 35.5, "longitude": -119.5,
                 "unknown_attrs": [], "is_open": True, "forest_name": None,
                 "is_free": False, "fee": "$20", "reservation_type": None,
                 "has_water": False, "toilet_type": None, "water_feature": None,
                 "terrain": None, "elevation_ft": None, "activities": [],
                 "weather_summary": None},
            ],
            start_date=None,
            end_date=None,
            forests=[],
            result_count=1,
        )

        import re
        match = re.search(r'id="searchData">([^<]+)<', result)
        assert match, "searchData JSON block missing"
        data = json.loads(match.group(1))
        assert "query" in data

    def test_positions_sequential_one_based(self):
        """Render three cards and verify positions are 1, 2, 3 (not 0, 1, 2)."""
        tpl_dir = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "templates")
        result = _render_results(
            query="test",
            campsites=[
                {"id": 101, "name": "Camp 1", "latitude": 35.5, "longitude": -119.5,
                 "unknown_attrs": [], "is_open": True, "forest_name": None,
                 "is_free": False, "fee": "$20", "reservation_type": None,
                 "has_water": False, "toilet_type": None, "water_feature": None,
                 "terrain": None, "elevation_ft": None, "activities": [],
                 "weather_summary": None},
                {"id": 102, "name": "Camp 2", "latitude": 35.6, "longitude": -119.6,
                 "unknown_attrs": [], "is_open": True, "forest_name": None,
                 "is_free": False, "fee": "$25", "reservation_type": None,
                 "has_water": True, "toilet_type": None, "water_feature": None,
                 "terrain": None, "elevation_ft": None, "activities": [],
                 "weather_summary": None},
                {"id": 103, "name": "Camp 3", "latitude": 35.7, "longitude": -119.7,
                 "unknown_attrs": [], "is_open": False, "forest_name": None,
                 "is_free": False, "fee": None, "reservation_type": None,
                 "has_water": False, "toilet_type": None, "water_feature": None,
                 "terrain": None, "elevation_ft": None, "activities": [],
                 "weather_summary": None},
            ],
            start_date=None,
            end_date=None,
            forests=[],
            result_count=3,
        )

        import re
        positions = re.findall(r'data-position="(\d+)"', result)
        assert positions == ["1", "2", "3"], (
            f"Positions should be [1, 2, 3], got {positions}")

        # Verify no position="0"
        assert 'data-position="0"' not in result, (
            "Position 0 found; positions must be 1-based")


class TestImpressionEventPayloadContract:
    """Tests that the JavaScript sends the exact payload the endpoint expects."""

    def test_result_impression_payload_accepted(self):
        """POST /api/events accepts result_impression with campsite_id, position, query_text."""
        with app_module.app.test_client() as client:
            with patch("app.log_event") as mock_log:
                events = [
                    {
                        "event_type": "result_impression",
                        "campsite_id": 123,
                        "position": 1,
                        "query_text": "tent camping",
                    },
                ]

                resp = client.post(
                    "/api/events",
                    json={"events": events},
                    content_type="application/json",
                )

                assert resp.status_code == 204
                mock_log.assert_called_once()
                call_args = mock_log.call_args[1]
                assert call_args["event_type"] == "result_impression"
                assert call_args["campsite_id"] == 123
                assert call_args["position"] == 1
                assert call_args["query_text"] == "tent camping"

    def test_result_clicked_payload_accepted(self):
        """POST /api/events accepts result_clicked with campsite_id, position, query_text."""
        with app_module.app.test_client() as client:
            with patch("app.log_event") as mock_log:
                events = [
                    {
                        "event_type": "result_clicked",
                        "campsite_id": 456,
                        "position": 2,
                        "query_text": "water features",
                    },
                ]

                resp = client.post(
                    "/api/events",
                    json={"events": events},
                    content_type="application/json",
                )

                assert resp.status_code == 204
                mock_log.assert_called_once()
                call_args = mock_log.call_args[1]
                assert call_args["event_type"] == "result_clicked"
                assert call_args["campsite_id"] == 456
                assert call_args["position"] == 2
                assert call_args["query_text"] == "water features"

    def test_batched_impressions_accepted(self):
        """POST /api/events accepts a batch of impression events."""
        with app_module.app.test_client() as client:
            with patch("app.log_event") as mock_log:
                events = [
                    {
                        "event_type": "result_impression",
                        "campsite_id": i,
                        "position": i,
                        "query_text": "camping",
                    }
                    for i in range(1, 6)
                ]

                resp = client.post(
                    "/api/events",
                    json={"events": events},
                    content_type="application/json",
                )

                assert resp.status_code == 204
                assert mock_log.call_count == 5

    def test_empty_query_text_accepted(self):
        """POST /api/events accepts impression with empty or missing query_text."""
        with app_module.app.test_client() as client:
            with patch("app.log_event") as mock_log:
                events = [
                    {
                        "event_type": "result_impression",
                        "campsite_id": 789,
                        "position": 1,
                        "query_text": "",
                    },
                ]

                resp = client.post(
                    "/api/events",
                    json={"events": events},
                    content_type="application/json",
                )

                assert resp.status_code == 204
                mock_log.assert_called_once()


class TestJavaScriptStructure:
    """Weak but present JS structural tests."""

    def test_impressions_double_send_guard_exists(self):
        """main.js must have a guard against sending impressions twice."""
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", "main.js"), encoding="utf-8") as f:
            js = f.read()

        # Check for the _impressions_sent flag
        assert "_impressions_sent" in js, (
            "main.js must have a _impressions_sent flag to guard against "
            "double-sending impressions")

        # Check that it's initialized to false
        assert "_impressions_sent = false" in js, (
            "_impressions_sent must be initialized to false")

        # Check that it's set to true after sending
        assert "if (_impressions_sent) return" in js, (
            "initImpressionTracking must guard against double-sending")

    def test_send_beacon_json_uses_blob(self):
        """main.js must build a Blob with application/json type for sendBeacon."""
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", "main.js"), encoding="utf-8") as f:
            js = f.read()

        # Check for Blob creation with json type
        assert 'new Blob' in js, (
            "main.js must use new Blob to create JSON payload for sendBeacon")

        assert 'type: "application/json"' in js, (
            'main.js must create Blob with type: "application/json"')

        # Check that sendBeacon uses the blob
        assert 'navigator.sendBeacon(url, blob)' in js, (
            "sendBeacon must be called with the Blob")


class TestSelectorTemplateCoupling:
    """The JS selector and the template attributes must stay in sync.

    main.js collects cards with document.querySelectorAll(
    "[data-position][data-campsite-id]") and skips any card missing either
    attribute. If the template stops emitting one of them, or emits them on
    different elements, the tracker silently sends nothing: no error, no
    console noise, just an empty table that looks like nobody used the site.
    That is the failure this project can least afford to discover six months
    from now, because the lost data cannot be backfilled.
    """

    def _rendered(self):
        rows = [{"id": 100 + i, "name": "C%d" % i, "latitude": 35.0,
                 "longitude": -119.0, "unknown_attrs": [], "is_open": True,
                 "forest_name": None, "is_free": False, "fee": "$20",
                 "reservation_type": None, "has_water": False,
                 "toilet_type": None, "water_feature": None, "terrain": None,
                 "elevation_ft": None, "activities": [],
                 "weather_summary": None} for i in (1, 2, 3)]
        return _render_results(query="lake", campsites=rows, result_count=3,
                               start_date="", end_date="")

    def _js(self):
        import os
        p = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "static", "js", "main.js")
        with open(p, encoding="utf-8") as fh:
            return fh.read()

    def test_js_selector_attributes_appear_on_one_element(self):
        import re
        html = self._rendered()
        both = re.findall(
            r'<[^>]*data-position="(\d+)"[^>]*data-campsite-id="(\d+)"[^>]*>',
            html) or re.findall(
            r'<[^>]*data-campsite-id="(\d+)"[^>]*data-position="(\d+)"[^>]*>',
            html)
        assert len(both) == 3, (
            "the two attributes main.js selects on must sit on the SAME "
            "element; found %d such elements" % len(both))

    def test_selector_in_js_still_matches_the_attributes_emitted(self):
        """If someone renames an attribute, this fails instead of going quiet."""
        js = self._js()
        assert '[data-position][data-campsite-id]' in js, (
            "main.js selector changed; update the template attributes to match")
        html = self._rendered()
        assert 'data-position="' in html and 'data-campsite-id="' in html

    def test_pick_tracking_attribute_survives(self):
        """data-pick feeds pick_count, which the search ranker already uses."""
        assert 'data-pick="' in self._rendered()
