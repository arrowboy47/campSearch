"""Tests for user event logging infrastructure (migration 0023).

Tests cover:
- db.log_event() function behavior and error handling
- Server-side events in /results and /campsite routes
- /api/events endpoint for browser events
- CSRF handling and security constraints
- Batch size limits and event type validation
"""

import pytest
import json
from uuid import UUID, uuid4
from unittest.mock import patch, MagicMock, Mock, call
import psycopg2

import app as app_module
import db as db_module


class TestLogEventFunction:
    """Tests for db.log_event() function."""

    def test_log_event_signature(self):
        """Verify log_event accepts the expected arguments."""
        # This should not raise
        with patch("db.get_connection") as mock_conn:
            mock_cursor = MagicMock()
            mock_conn.return_value.cursor.return_value = mock_cursor
            db_module.log_event(
                event_type="search_performed",
                user_id=1,
                anon_id=str(uuid4()),
                session_id=str(uuid4()),
                campsite_id=123,
                position=1,
                query_text="camping",
                filters={"forest": "test"},
                result_ids=[1, 2, 3],
                meta={"count": 3},
            )

    def test_log_event_inserts_with_correct_columns(self):
        """log_event inserts into user_events with the right columns."""
        with patch("db.get_connection") as mock_get_conn:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_get_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            test_anon = str(uuid4())
            test_session = str(uuid4())

            db_module.log_event(
                event_type="search_performed",
                anon_id=test_anon,
                session_id=test_session,
                query_text="tent camping",
                filters={"forest": "sierra"},
                result_ids=[10, 20, 30],
                meta={"count": 3},
            )

            # Verify execute was called with an INSERT statement
            mock_cursor.execute.assert_called_once()
            sql, params = mock_cursor.execute.call_args[0]

            assert "INSERT INTO user_events" in sql
            assert "user_id" in sql
            assert "anon_id" in sql
            assert "session_id" in sql
            assert "event_type" in sql
            assert "query_text" in sql
            assert "filters" in sql
            assert "result_ids" in sql
            assert "meta" in sql

            # Params should match what we passed
            assert params[1] == test_anon  # anon_id
            assert params[2] == test_session  # session_id
            assert params[3] == "search_performed"  # event_type
            assert params[6] == "tent camping"  # query_text

            # Verify commit and close were called
            mock_conn.commit.assert_called_once()
            mock_conn.close.assert_called_once()

    def test_log_event_closes_connection_on_failure(self):
        """log_event closes the connection even when database raises."""
        with patch("db.get_connection") as mock_get_conn:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_get_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            # Simulate database error on execute
            mock_cursor.execute.side_effect = psycopg2.DatabaseError("test error")

            # Should not raise
            db_module.log_event(
                event_type="search_performed",
                anon_id=str(uuid4()),
            )

            # Connection should still be closed (this is critical: prevents leaks)
            mock_conn.close.assert_called_once()

    def test_log_event_rejects_unknown_event_type(self):
        """log_event silently rejects an unknown event_type."""
        with patch("db.get_connection") as mock_get_conn:
            db_module.log_event(
                event_type="unknown_event_type",
                anon_id=str(uuid4()),
            )

            # Should never open a connection for an unknown type
            mock_get_conn.assert_not_called()

    def test_log_event_accepts_all_allowed_types(self):
        """log_event accepts all nine allowed event types."""
        with patch("db.get_connection") as mock_get_conn:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_get_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            for event_type in db_module.ALLOWED_EVENT_TYPES:
                mock_get_conn.reset_mock()
                mock_cursor.reset_mock()

                db_module.log_event(event_type=event_type)

                # Should have opened a connection
                mock_get_conn.assert_called_once()

    def test_log_event_with_none_meta_becomes_empty_dict(self):
        """When meta is None, it should become an empty dict."""
        with patch("db.get_connection") as mock_get_conn:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_get_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            db_module.log_event(
                event_type="search_performed",
                anon_id=str(uuid4()),
                meta=None,
            )

            sql, params = mock_cursor.execute.call_args[0]
            # meta param should be an empty dict wrapped in Json
            # (Position 9 in the params tuple: 0=user, 1=anon, 2=session,
            # 3=type, 4=campsite, 5=position, 6=query, 7=filters, 8=result_ids, 9=meta)
            assert params[9] is not None  # Json wrapper


class TestServerSideSearchPerformed:
    """Tests for search_performed event in /results route."""

    def test_results_logs_search_performed(self):
        """The /results route logs a search_performed event."""
        with app_module.app.test_client() as client:
            with patch("app.search_campsites") as mock_search:
                with patch("app.log_event") as mock_log:
                    mock_search.return_value = [
                        {"id": 1, "name": "Camp A"},
                        {"id": 2, "name": "Camp B"},
                    ]

                    resp = client.get("/results?query=tent")

                    assert resp.status_code == 200
                    # Verify log_event was called with search_performed
                    mock_log.assert_called()
                    call_args = mock_log.call_args

                    assert call_args[1]["event_type"] == "search_performed"
                    assert call_args[1]["query_text"] == "tent"
                    assert call_args[1]["result_ids"] == [1, 2]
                    assert call_args[1]["meta"]["result_count"] == 2

    def test_results_includes_filters_in_event(self):
        """The search_performed event includes parsed filters."""
        with app_module.app.test_client() as client:
            with patch("app.search_campsites") as mock_search:
                with patch("app.log_event") as mock_log:
                    mock_search.return_value = []

                    resp = client.get("/results?forest=sierra&water=true")

                    assert resp.status_code == 200
                    mock_log.assert_called()
                    call_args = mock_log.call_args

                    assert call_args[1]["event_type"] == "search_performed"
                    assert call_args[1]["filters"]["forest"] == "sierra"
                    assert call_args[1]["filters"]["water"] is True

    def test_results_includes_session_id_in_event(self):
        """The search_performed event includes session_id."""
        with app_module.app.test_client() as client:
            with patch("app.search_campsites") as mock_search:
                with patch("app.log_event") as mock_log:
                    with patch("app.get_session_id") as mock_session:
                        mock_search.return_value = []
                        test_session = str(uuid4())
                        mock_session.return_value = test_session

                        resp = client.get("/results")

                        assert resp.status_code == 200
                        mock_log.assert_called()
                        assert mock_log.call_args[1]["session_id"] == test_session

    def test_results_logs_even_with_empty_results(self):
        """search_performed is logged even when there are no results."""
        with app_module.app.test_client() as client:
            with patch("app.search_campsites") as mock_search:
                with patch("app.log_event") as mock_log:
                    mock_search.return_value = []

                    resp = client.get("/results?query=nowhere")

                    assert resp.status_code == 200
                    mock_log.assert_called()
                    assert mock_log.call_args[1]["result_ids"] == []


class TestServerSideCampsiteViewed:
    """Tests for campsite_viewed event in /campsite route."""

    def test_campsite_logs_campsite_viewed(self):
        """The /campsite route logs a campsite_viewed event."""
        with app_module.app.test_client() as client:
            with patch("app.get_campsite_by_id") as mock_get:
                with patch("app.log_event") as mock_log:
                    mock_get.return_value = {
                        "id": 42,
                        "name": "Test Camp",
                        "latitude": 37.5,
                        "longitude": -119.5,
                    }

                    resp = client.get("/campsite/42")

                    assert resp.status_code == 200
                    mock_log.assert_called()
                    call_args = mock_log.call_args

                    assert call_args[1]["event_type"] == "campsite_viewed"
                    assert call_args[1]["campsite_id"] == 42

    def test_campsite_includes_session_id(self):
        """The campsite_viewed event includes session_id."""
        with app_module.app.test_client() as client:
            with patch("app.get_campsite_by_id") as mock_get:
                with patch("app.log_event") as mock_log:
                    with patch("app.get_session_id") as mock_session:
                        mock_get.return_value = {
                            "id": 99,
                            "name": "Test",
                            "latitude": 0,
                            "longitude": 0,
                        }
                        test_session = str(uuid4())
                        mock_session.return_value = test_session

                        resp = client.get("/campsite/99")

                        assert resp.status_code == 200
                        mock_log.assert_called()
                        assert mock_log.call_args[1]["session_id"] == test_session

    def test_campsite_404_does_not_log(self):
        """A 404 (campsite not found) does not log an event."""
        with app_module.app.test_client() as client:
            with patch("app.get_campsite_by_id") as mock_get:
                with patch("app.log_event") as mock_log:
                    mock_get.return_value = None

                    resp = client.get("/campsite/99999")

                    assert resp.status_code == 404
                    mock_log.assert_not_called()


class TestRouteLoggingDoesNotBreakPage:
    """Tests that logging errors don't break the page."""

    def test_search_results_page_works_even_if_logging_fails(self):
        """The /results page renders even if log_event raises."""
        with app_module.app.test_client() as client:
            with patch("app.search_campsites") as mock_search:
                with patch("app.log_event") as mock_log:
                    mock_search.return_value = [
                        {"id": 1, "name": "Camp A"},
                    ]
                    # Make log_event raise
                    mock_log.side_effect = Exception("logging failed")

                    # Should still return 200
                    resp = client.get("/results?query=test")
                    assert resp.status_code == 200

    def test_campsite_page_works_even_if_logging_fails(self):
        """The /campsite page renders even if log_event raises."""
        with app_module.app.test_client() as client:
            with patch("app.get_campsite_by_id") as mock_get:
                with patch("app.log_event") as mock_log:
                    mock_get.return_value = {
                        "id": 42,
                        "name": "Test",
                        "latitude": 0,
                        "longitude": 0,
                    }
                    # Make log_event raise
                    mock_log.side_effect = Exception("logging failed")

                    # Should still return 200
                    resp = client.get("/campsite/42")
                    assert resp.status_code == 200


class TestApiEventsEndpoint:
    """Tests for POST /api/events endpoint."""

    def test_api_events_accepts_valid_batch(self):
        """POST /api/events with valid batch logs each event."""
        with app_module.app.test_client() as client:
            with patch("app.log_event") as mock_log:
                events = [
                    {
                        "event_type": "result_impression",
                        "campsite_id": 1,
                        "position": 1,
                    },
                    {
                        "event_type": "result_clicked",
                        "campsite_id": 2,
                        "position": 2,
                    },
                ]

                resp = client.post(
                    "/api/events",
                    json={"events": events},
                    content_type="application/json",
                )

                assert resp.status_code == 204
                assert mock_log.call_count == 2

                # Verify both events were logged
                calls = mock_log.call_args_list
                assert calls[0][1]["event_type"] == "result_impression"
                assert calls[0][1]["campsite_id"] == 1
                assert calls[1][1]["event_type"] == "result_clicked"
                assert calls[1][1]["campsite_id"] == 2

    def test_api_events_returns_204_on_success(self):
        """POST /api/events returns 204 No Content."""
        with app_module.app.test_client() as client:
            with patch("app.log_event"):
                resp = client.post(
                    "/api/events",
                    json={"events": []},
                    content_type="application/json",
                )

                assert resp.status_code == 204
                assert resp.data == b""

    def test_api_events_rejects_server_only_event_types(self):
        """POST /api/events rejects server-generated event types."""
        with app_module.app.test_client() as client:
            with patch("app.log_event") as mock_log:
                events = [
                    {
                        "event_type": "search_performed",  # Server only!
                        "campsite_id": 1,
                    },
                ]

                resp = client.post(
                    "/api/events",
                    json={"events": events},
                    content_type="application/json",
                )

                assert resp.status_code == 204
                # Should not have logged anything
                mock_log.assert_not_called()

    def test_api_events_caps_batch_at_100(self):
        """POST /api/events ignores events beyond 100."""
        with app_module.app.test_client() as client:
            with patch("app.log_event") as mock_log:
                # Create 150 events
                events = [
                    {
                        "event_type": "result_impression",
                        "campsite_id": i,
                        "position": i,
                    }
                    for i in range(150)
                ]

                resp = client.post(
                    "/api/events",
                    json={"events": events},
                    content_type="application/json",
                )

                assert resp.status_code == 204
                # Should only log 100
                assert mock_log.call_count == 100

    def test_api_events_fills_server_ids(self):
        """POST /api/events fills user_id, anon_id, session_id from server."""
        with app_module.app.test_client() as client:
            with patch("app.log_event") as mock_log:
                with patch("app.current_actor") as mock_actor:
                    with patch("app.get_session_id") as mock_session:
                        mock_actor.return_value = (1, None)  # Signed-in user
                        mock_session.return_value = str(uuid4())

                        events = [
                            {"event_type": "result_impression", "campsite_id": 1}
                        ]

                        resp = client.post(
                            "/api/events",
                            json={"events": events},
                            content_type="application/json",
                        )

                        assert resp.status_code == 204
                        mock_log.assert_called()

                        # Verify server-generated fields were used
                        call_kwargs = mock_log.call_args[1]
                        assert call_kwargs["user_id"] == 1
                        assert call_kwargs["anon_id"] is None
                        assert call_kwargs["session_id"] == mock_session.return_value

    def test_api_events_ignores_client_supplied_user_id(self):
        """POST /api/events ignores user_id supplied by the browser."""
        with app_module.app.test_client() as client:
            with patch("app.log_event") as mock_log:
                with patch("app.current_actor") as mock_actor:
                    with patch("app.get_session_id"):
                        mock_actor.return_value = (None, str(uuid4()))  # Anonymous

                        events = [
                            {
                                "event_type": "result_impression",
                                "campsite_id": 1,
                                "user_id": 999,  # Attacker tries to set user_id
                            }
                        ]

                        resp = client.post(
                            "/api/events",
                            json={"events": events},
                            content_type="application/json",
                        )

                        assert resp.status_code == 204
                        # Verify user_id was NOT taken from request body
                        call_kwargs = mock_log.call_args[1]
                        assert call_kwargs["user_id"] is None  # Should be None (anon)
                        assert call_kwargs["user_id"] != 999

    def test_api_events_handles_malformed_json(self):
        """POST /api/events handles malformed JSON gracefully."""
        with app_module.app.test_client() as client:
            with patch("app.log_event") as mock_log:
                resp = client.post(
                    "/api/events",
                    data="not json at all",
                    content_type="application/json",
                )

                # Should still return 204
                assert resp.status_code == 204
                mock_log.assert_not_called()

    def test_api_events_handles_missing_events_key(self):
        """POST /api/events handles missing 'events' key."""
        with app_module.app.test_client() as client:
            with patch("app.log_event") as mock_log:
                resp = client.post(
                    "/api/events",
                    json={"not_events": []},
                    content_type="application/json",
                )

                assert resp.status_code == 204
                mock_log.assert_not_called()

    def test_api_events_handles_non_list_events(self):
        """POST /api/events handles events that is not a list."""
        with app_module.app.test_client() as client:
            with patch("app.log_event") as mock_log:
                resp = client.post(
                    "/api/events",
                    json={"events": "not a list"},
                    content_type="application/json",
                )

                assert resp.status_code == 204
                mock_log.assert_not_called()

    def test_api_events_ignores_non_dict_items(self):
        """POST /api/events ignores items in the batch that aren't dicts."""
        with app_module.app.test_client() as client:
            with patch("app.log_event") as mock_log:
                events = [
                    {"event_type": "result_impression", "campsite_id": 1},
                    "not a dict",
                    {"event_type": "result_clicked", "campsite_id": 2},
                ]

                resp = client.post(
                    "/api/events",
                    json={"events": events},
                    content_type="application/json",
                )

                assert resp.status_code == 204
                # Should have logged 2 valid events, skipped the string
                assert mock_log.call_count == 2

    def test_api_events_allowed_event_types(self):
        """POST /api/events only accepts browser-allowed event types."""
        allowed = {"result_impression", "result_clicked", "campsite_viewed", "search_feedback"}

        with app_module.app.test_client() as client:
            with patch("app.log_event") as mock_log:
                for event_type in allowed:
                    mock_log.reset_mock()

                    events = [{"event_type": event_type, "campsite_id": 1}]

                    resp = client.post(
                        "/api/events",
                        json={"events": events},
                        content_type="application/json",
                    )

                    assert resp.status_code == 204
                    mock_log.assert_called_once()


class TestGetSessionId:
    """Tests for the get_session_id() helper."""

    def test_session_id_is_minted_on_first_use(self):
        """First call to get_session_id() creates a new UUID."""
        with app_module.app.test_request_context("/"):
            sess_id = app_module.get_session_id()

            # Should be a valid UUID
            UUID(sess_id)
            assert len(sess_id) == 36  # UUID string length

    def test_session_id_is_reused_within_same_session(self):
        """Multiple calls in the same session return the same ID."""
        with app_module.app.test_request_context("/"):
            sess_id_1 = app_module.get_session_id()
            sess_id_2 = app_module.get_session_id()

            assert sess_id_1 == sess_id_2

    def test_session_id_differs_across_sessions(self):
        """Different requests get different session IDs."""
        with app_module.app.test_request_context("/"):
            sess_id_1 = app_module.get_session_id()

        with app_module.app.test_request_context("/"):
            sess_id_2 = app_module.get_session_id()

            assert sess_id_1 != sess_id_2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestApiOriginGuard:
    """Cross-site POSTs to beacon endpoints must be rejected.

    /api/ routes cannot carry a CSRF token, because navigator.sendBeacon
    cannot set headers. The exemption that allowed that was written when these
    endpoints were "unauthenticated and change nothing per-user". /api/events
    broke that premise: it attributes behaviour to the signed-in user, so
    without a guard any site a user visits can write fabricated events into
    their account and poison both their personalisation and the aggregate
    signals the ranking model learns from. /api/campsite/<id>/pick is the same
    shape, where forged picks inflate a campsite's search ranking.
    """

    def _client(self, monkeypatch, sink):
        import app as A, db
        monkeypatch.setattr(db, "log_event", lambda **kw: sink.append(kw))
        monkeypatch.setattr(A, "log_event", lambda **kw: sink.append(kw))
        A.app.config["TESTING"] = True
        return A.app.test_client()

    def test_cross_origin_post_is_rejected(self, monkeypatch):
        sink = []
        c = self._client(monkeypatch, sink)
        r = c.post("/api/events",
                   json={"events": [{"event_type": "result_clicked",
                                     "campsite_id": 1, "position": 1}]},
                   headers={"Origin": "https://evil.example"})
        assert r.status_code == 403, "a cross-origin beacon POST must be refused"
        assert sink == [], "nothing may be written for a cross-origin request"

    def test_same_origin_post_is_allowed(self, monkeypatch):
        sink = []
        c = self._client(monkeypatch, sink)
        r = c.post("/api/events",
                   json={"events": [{"event_type": "result_clicked",
                                     "campsite_id": 1, "position": 1}]},
                   headers={"Origin": "http://localhost"})
        assert r.status_code == 204
        assert len(sink) == 1

    def test_absent_origin_is_allowed(self, monkeypatch):
        """sendBeacon and non-browser clients may omit Origin entirely."""
        sink = []
        c = self._client(monkeypatch, sink)
        r = c.post("/api/events",
                   json={"events": [{"event_type": "result_clicked",
                                     "campsite_id": 1, "position": 1}]})
        assert r.status_code == 204
        assert len(sink) == 1

    def test_pick_endpoint_also_guarded(self, monkeypatch):
        """Forged picks inflate a campsite's ranking, so guard it too."""
        import db
        bumped = []
        monkeypatch.setattr(db, "record_pick", lambda cid: bumped.append(cid))
        import app as A
        monkeypatch.setattr(A, "record_pick", lambda cid: bumped.append(cid))
        A.app.config["TESTING"] = True
        c = A.app.test_client()
        r = c.post("/api/campsite/1/pick",
                   headers={"Origin": "https://evil.example"})
        assert r.status_code == 403
        assert bumped == []
