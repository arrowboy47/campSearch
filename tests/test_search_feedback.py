"""Tests for search feedback controls on results page.

Tests cover:
- Template rendering of query-level and per-result feedback controls
- Accessibility labels on feedback controls
- JavaScript payload shape for feedback events
- Endpoint contract for search_feedback events
- Preventing duplicate votes on the same target
- Server-side derivation of user_id, anon_id, session_id
"""

import pytest
import json
import re
from uuid import uuid4
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
    with app_module.app.test_request_context("/results"):
        tpl = app_module.app.jinja_env.get_template("results.html")
        return tpl.render(**context)


@pytest.fixture(autouse=True)
def no_db(monkeypatch):
    """Stub every database call the page routes make.

    See test_event_logging.py for rationale: this suite's value is that it
    runs with no database and no network.
    """
    import app as A
    stubs = {
        "get_campsite_by_id": {"id": 42, "name": "Test",
                               "latitude": 0, "longitude": 0},
        "get_trails_for_campsite": [],
        "get_forecast": None,
        "is_campsite_saved": False,
        "get_collections": [],
        "search_campsites": [],
        "get_facet_options": {},
        "get_all_forests": [],
        "get_reviews_for_campsite": [],
        "count_reviews_for_campsite": 0,
        "get_review_verdict_counts": {"up": 0, "down": 0},
        "get_review_photos": [],
        "get_review_by_user_and_campsite": None,
        "get_pending_photos": [],
        "get_open_reports": [],
        "get_attribute_report_groups": [],
    }
    for name, value in stubs.items():
        if hasattr(A, name):
            monkeypatch.setattr(A, name, (lambda v: (lambda *a, **k: v))(value))
    return stubs


class TestFeedbackTemplateControls:
    """Tests for feedback controls rendered in results.html."""

    def test_query_level_feedback_prompt_renders(self):
        """The results page renders a query-level feedback prompt."""
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

        # Query-level feedback must be present
        assert "Did you find what you were looking for?" in result or \
               "search_feedback" in result, (
                   "Query-level feedback prompt must be rendered")

    def test_per_result_feedback_controls_render(self):
        """Each result card renders a per-result feedback control."""
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
            ],
            start_date=None,
            end_date=None,
            forests=[],
            result_count=2,
        )

        # Count feedback controls (thumbs up/down)
        feedback_controls = re.findall(
            r'data-feedback|data-vote|aria-label=".*(?:helpful|unhelpful|up|down)"',
            result, re.IGNORECASE)
        assert len(feedback_controls) >= 2, (
            "Each result card must have a feedback control")

    def test_feedback_controls_have_accessible_labels(self):
        """Feedback controls must have aria-label or visible text, not bare icons."""
        result = _render_results(
            query="test",
            campsites=[
                {"id": 101, "name": "Camp 1", "latitude": 35.5, "longitude": -119.5,
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

        # Look for aria-label or text content on feedback elements
        has_labels = (
            'aria-label=' in result or
            'title=' in result or
            ('👍' in result and '👎' in result)  # visual indication
        )
        assert has_labels, (
            "Feedback controls must have accessible labels (aria-label, title, or text)")


class TestSearchFeedbackPayloadContract:
    """Tests for the JSON payload shape sent to /api/events."""

    def test_query_level_feedback_payload_structure(self):
        """Query-level feedback sends the correct payload shape."""
        with app_module.app.test_client() as client:
            with patch("app.log_event") as mock_log:
                # Payload structure for a query-level feedback (Yes/No)
                events = [
                    {
                        "event_type": "search_feedback",
                        "query_text": "tent camping",
                        "meta": {
                            "feedback_type": "query_level",
                            "verdict": "yes"
                        },
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
                assert call_args["event_type"] == "search_feedback"
                assert call_args["query_text"] == "tent camping"
                assert call_args["meta"]["feedback_type"] == "query_level"
                assert call_args["meta"]["verdict"] in ("yes", "no")

    def test_per_result_feedback_payload_structure(self):
        """Per-result feedback sends campsite_id, position, query, and verdict."""
        with app_module.app.test_client() as client:
            with patch("app.log_event") as mock_log:
                # Payload structure for per-result feedback (thumbs up/down)
                events = [
                    {
                        "event_type": "search_feedback",
                        "campsite_id": 123,
                        "position": 2,
                        "query_text": "lake camping",
                        "meta": {
                            "feedback_type": "result_level",
                            "verdict": "helpful"
                        },
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
                assert call_args["event_type"] == "search_feedback"
                assert call_args["campsite_id"] == 123
                assert call_args["position"] == 2
                assert call_args["query_text"] == "lake camping"
                assert call_args["meta"]["feedback_type"] == "result_level"
                assert call_args["meta"]["verdict"] in ("helpful", "unhelpful")

    def test_server_overrides_client_user_id(self):
        """POST /api/events overrides client-supplied user_id with server value."""
        with app_module.app.test_client() as client:
            with patch("app.log_event") as mock_log:
                with patch("app.current_actor") as mock_actor:
                    with patch("app.get_session_id"):
                        mock_actor.return_value = (42, None)  # Signed-in user

                        events = [
                            {
                                "event_type": "search_feedback",
                                "campsite_id": 1,
                                "position": 1,
                                "query_text": "test",
                                "user_id": 999,  # Client tries to set this
                                "meta": {"verdict": "helpful"},
                            }
                        ]

                        resp = client.post(
                            "/api/events",
                            json={"events": events},
                            content_type="application/json",
                        )

                        assert resp.status_code == 204
                        # Verify server-provided user_id was used, not client's
                        call_kwargs = mock_log.call_args[1]
                        assert call_kwargs["user_id"] == 42
                        assert call_kwargs["user_id"] != 999

    def test_server_derives_session_id(self):
        """POST /api/events derives session_id server-side."""
        with app_module.app.test_client() as client:
            with patch("app.log_event") as mock_log:
                with patch("app.current_actor") as mock_actor:
                    with patch("app.get_session_id") as mock_session:
                        mock_actor.return_value = (None, str(uuid4()))  # Anonymous
                        test_session = str(uuid4())
                        mock_session.return_value = test_session

                        events = [
                            {
                                "event_type": "search_feedback",
                                "campsite_id": 1,
                                "meta": {"verdict": "helpful"},
                            }
                        ]

                        resp = client.post(
                            "/api/events",
                            json={"events": events},
                            content_type="application/json",
                        )

                        assert resp.status_code == 204
                        call_kwargs = mock_log.call_args[1]
                        assert call_kwargs["session_id"] == test_session


class TestDuplicateVotePrevention:
    """Tests for preventing duplicate votes on the same target."""

    def test_js_has_duplicate_prevention_guard(self):
        """main.js must contain a guard preventing duplicate votes."""
        import os
        with open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "js", "main.js"), encoding="utf-8") as f:
            js = f.read()

        # Check for some form of duplicate guard (could be a Set, Map, or flags)
        has_guard = (
            "_feedback_votes" in js or
            "feedbackVotes" in js or
            "voted" in js or
            "feedback_recorded" in js or
            "_voted_targets" in js
        )
        assert has_guard, (
            "main.js must track which targets have already received a vote "
            "to prevent duplicates")


class TestSelectorTemplateCouplingFeedback:
    """Per-result feedback relies on the same data-position and data-campsite-id
    attributes that impression tracking uses. If the template changes them,
    feedback stops working silently.
    """

    def test_result_cards_still_have_both_tracking_attributes(self):
        """Verify [data-position][data-campsite-id] selector still works."""
        result = _render_results(
            query="test",
            campsites=[
                {"id": 100, "name": "C1", "latitude": 35.0, "longitude": -119.0,
                 "unknown_attrs": [], "is_open": True, "forest_name": None,
                 "is_free": False, "fee": "$20", "reservation_type": None,
                 "has_water": False, "toilet_type": None, "water_feature": None,
                 "terrain": None, "elevation_ft": None, "activities": [],
                 "weather_summary": None},
                {"id": 101, "name": "C2", "latitude": 35.0, "longitude": -119.0,
                 "unknown_attrs": [], "is_open": True, "forest_name": None,
                 "is_free": False, "fee": "$20", "reservation_type": None,
                 "has_water": False, "toilet_type": None, "water_feature": None,
                 "terrain": None, "elevation_ft": None, "activities": [],
                 "weather_summary": None},
            ],
            result_count=2,
            start_date="",
            end_date="",
        )

        # Count cards with both attributes on the same element
        both = re.findall(
            r'<[^>]*data-position="(\d+)"[^>]*data-campsite-id="(\d+)"[^>]*>',
            result) or re.findall(
            r'<[^>]*data-campsite-id="(\d+)"[^>]*data-position="(\d+)"[^>]*>',
            result)

        assert len(both) >= 2, (
            "Result cards must have both data-position and data-campsite-id "
            "on the same element for feedback tracking")
