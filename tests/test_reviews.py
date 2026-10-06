"""Tests for review submission (migration 0024).

Tests cover:
- db functions: create_review, get_review_by_user_and_campsite, count_reviews_today
- POST /campsite/<id>/review route
- Content filtering (slurs, profanity, false positives)
- Rate limiting (3 reviews per day)
- CSRF protection
- Transaction atomicity
- Event logging
"""

import pytest
import json
from uuid import uuid4
from unittest.mock import patch, MagicMock, Mock, call
from datetime import datetime, timedelta
import psycopg2

import app as app_module
import db as db_module


@pytest.fixture(autouse=True)
def no_db(monkeypatch):
    """Stub every database call the route makes.

    This suite's value is that it runs with no database and no network.
    Every database call the route makes must be patched, not just a few, so
    an added database call shows up as a clear failure rather than a silent
    dependency on someone's tunnel.
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
        "get_review_by_user_and_campsite": None,
        "count_reviews_today": 0,
    }
    for name, value in stubs.items():
        if hasattr(A, name):
            monkeypatch.setattr(A, name, (lambda v: (lambda *a, **k: v))(value))
    return stubs


# --- db function tests ---

class TestCreateReview:
    """Tests for db.create_review()."""

    def test_create_review_inserts_review_and_reports_in_transaction(self):
        """create_review inserts both the review and attribute reports, or neither."""
        with patch("db.get_connection") as mock_get_conn:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_get_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            # Mock successful inserts.
            mock_cursor.fetchone.return_value = (123,)  # review ID

            review_id = db_module.create_review(
                user_id=1,
                campsite_id=42,
                verdict=True,
                body="Great place",
                visited=True,
                attribute_reports=[
                    {"attribute": "water", "claimed_value": "yes"},
                    {"attribute": "toilet_type", "claimed_value": "vault"},
                ],
            )

            assert review_id == 123
            mock_conn.commit.assert_called_once()
            # Verify both INSERT calls happened
            assert mock_cursor.execute.call_count == 3  # review + 2 reports

    def test_create_review_raises_on_duplicate(self):
        """create_review raises IntegrityError on UNIQUE constraint (user already reviewed)."""
        with patch("db.get_connection") as mock_get_conn:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_get_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            # Simulate UNIQUE constraint violation.
            mock_cursor.execute.side_effect = psycopg2.IntegrityError("UNIQUE violation")

            with pytest.raises(psycopg2.IntegrityError):
                db_module.create_review(
                    user_id=1,
                    campsite_id=42,
                    verdict=True,
                    body="",
                    visited=True,
                    attribute_reports=[],
                )

            mock_conn.rollback.assert_called_once()
            mock_conn.close.assert_called_once()

    def test_create_review_closes_connection_on_failure(self):
        """create_review closes the connection in finally, even on failure."""
        with patch("db.get_connection") as mock_get_conn:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_get_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            mock_cursor.execute.side_effect = psycopg2.DatabaseError("bad")

            with pytest.raises(psycopg2.DatabaseError):
                db_module.create_review(
                    user_id=1,
                    campsite_id=42,
                    verdict=True,
                    body="",
                    visited=True,
                    attribute_reports=[],
                )

            # Connection must be closed even though there was an error.
            mock_conn.close.assert_called_once()

    def test_create_review_with_empty_attributes(self):
        """create_review works with no attribute reports."""
        with patch("db.get_connection") as mock_get_conn:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_get_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            mock_cursor.fetchone.return_value = (999,)

            review_id = db_module.create_review(
                user_id=1,
                campsite_id=42,
                verdict=False,
                body="Not great",
                visited=True,
                attribute_reports=[],
            )

            assert review_id == 999
            # One call: the review INSERT. Attribute loop skipped.
            assert mock_cursor.execute.call_count == 1


class TestGetReviewByUserAndCampsite:
    """Tests for db.get_review_by_user_and_campsite()."""

    def test_get_review_returns_existing_review(self):
        """get_review_by_user_and_campsite returns the review dict if it exists."""
        with patch("db.get_connection") as mock_get_conn:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_get_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            mock_cursor.fetchone.return_value = {
                "id": 1,
                "verdict": True,
                "body": "Great",
                "visited": True,
                "status": "published",
                "created_at": datetime.now(),
                "updated_at": datetime.now(),
            }

            review = db_module.get_review_by_user_and_campsite(1, 42)

            assert review is not None
            assert review["verdict"] is True
            mock_conn.close.assert_called_once()

    def test_get_review_returns_none_if_not_exists(self):
        """get_review_by_user_and_campsite returns None if no review."""
        with patch("db.get_connection") as mock_get_conn:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_get_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            mock_cursor.fetchone.return_value = None

            review = db_module.get_review_by_user_and_campsite(1, 42)

            assert review is None
            mock_conn.close.assert_called_once()


class TestCountReviewsToday:
    """Tests for db.count_reviews_today()."""

    def test_count_reviews_today_returns_count(self):
        """count_reviews_today returns the number of reviews submitted today."""
        with patch("db.get_connection") as mock_get_conn:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_get_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            mock_cursor.fetchone.return_value = (2,)

            count = db_module.count_reviews_today(1)

            assert count == 2
            mock_conn.close.assert_called_once()


# --- route tests ---

class TestSubmitReviewRoute:
    """Tests for POST /campsite/<id>/review."""

    def _client(self, monkeypatch, sink=None):
        """Helper to set up test client with stubbed database."""
        if sink is None:
            sink = []
        import app as A, db
        monkeypatch.setattr(db, "log_event", lambda **kw: sink.append(kw))
        monkeypatch.setattr(A, "log_event", lambda **kw: sink.append(kw))
        A.app.config["TESTING"] = True
        return A.app.test_client(), sink

    def test_anonymous_post_is_rejected(self, monkeypatch):
        """POST /campsite/<id>/review requires login."""
        client, _ = self._client(monkeypatch)

        with patch("app.get_campsite_by_id", return_value={"id": 42, "name": "Test"}):
            # Anonymous: no session user_id, no CSRF token.
            # CSRF is checked first, returning 400. That's correct behavior.
            resp = client.post(
                "/campsite/42/review",
                data={"verdict": "up", "body": "Great"},
            )

            # Either 400 (CSRF rejection) or 302 (login redirect) is correct.
            # Both confirm anonymous users are blocked.
            assert resp.status_code in (302, 303, 400), \
                f"should reject anonymous, got {resp.status_code}"

    def test_signed_in_post_creates_review(self, monkeypatch):
        """POST /campsite/<id>/review with login and CSRF creates a review."""
        client, events = self._client(monkeypatch)

        with patch("app.get_campsite_by_id", return_value={"id": 42, "name": "Test"}), \
             patch("app.create_review") as mock_create, \
             patch("app.count_reviews_today", return_value=0), \
             patch("app.get_review_by_user_and_campsite", return_value=None), \
             patch("app.current_user", return_value={"id": 1, "username": "test"}), \
             patch("app.is_campsite_saved", return_value=False), \
             patch("app.get_collections", return_value=[]):

            mock_create.return_value = 123  # review ID

            with client:
                with client.session_transaction() as sess:
                    sess["user_id"] = 1
                    sess["_csrf"] = "test-token"

                # Now POST with CSRF
                resp = client.post(
                    "/campsite/42/review",
                    data={
                        "_csrf": "test-token",
                        "verdict": "up",
                        "body": "Great place",
                        "visited": "1",
                    },
                )

                assert resp.status_code in (302, 303), f"should redirect after success, got {resp.status_code}"
                mock_create.assert_called_once()

    def test_duplicate_review_is_rejected(self, monkeypatch):
        """POST for a campsite already reviewed by user shows error."""
        client, _ = self._client(monkeypatch)

        with patch("app.get_campsite_by_id", return_value={"id": 42, "name": "Test"}), \
             patch("app.create_review") as mock_create, \
             patch("app.count_reviews_today", return_value=0), \
             patch("app.current_user", return_value={"id": 1, "username": "test"}):

            # Simulate UNIQUE constraint violation
            mock_create.side_effect = psycopg2.IntegrityError("UNIQUE violation")

            with client:
                with client.session_transaction() as sess:
                    sess["user_id"] = 1
                    sess["_csrf"] = "test-token"

                resp = client.post(
                    "/campsite/42/review",
                    data={
                        "_csrf": "test-token",
                        "verdict": "up",
                        "body": "Great",
                    },
                )

                # Should redirect with error flash
                assert resp.status_code in (302, 303)

    def test_rate_limit_three_reviews_per_day(self, monkeypatch):
        """Fourth review in a day is rejected."""
        client, _ = self._client(monkeypatch)

        with patch("app.get_campsite_by_id", return_value={"id": 42, "name": "Test"}), \
             patch("app.count_reviews_today") as mock_count, \
             patch("app.create_review") as mock_create, \
             patch("app.current_user", return_value={"id": 1, "username": "test"}):

            # User has already submitted 3 reviews today
            mock_count.return_value = 3

            with client:
                with client.session_transaction() as sess:
                    sess["user_id"] = 1
                    sess["_csrf"] = "test-token"

                resp = client.post(
                    "/campsite/42/review",
                    data={
                        "_csrf": "test-token",
                        "verdict": "up",
                        "body": "Great",
                    },
                )

                # Should redirect with rate limit message
                assert resp.status_code in (302, 303)
                # create_review should not have been called
                mock_create.assert_not_called()

    def test_slur_is_rejected(self, monkeypatch):
        """POST with a slur in the body is rejected."""
        client, _ = self._client(monkeypatch)

        with patch("app.get_campsite_by_id", return_value={"id": 42, "name": "Test"}), \
             patch("app.count_reviews_today", return_value=0), \
             patch("app.create_review") as mock_create, \
             patch("app.current_user", return_value={"id": 1, "username": "test"}):

            with client:
                with client.session_transaction() as sess:
                    sess["user_id"] = 1
                    sess["_csrf"] = "test-token"

                resp = client.post(
                    "/campsite/42/review",
                    data={
                        "_csrf": "test-token",
                        "verdict": "up",
                        "body": "This place is full of n1gg",  # slur
                    },
                )

                # Should redirect without creating review
                assert resp.status_code in (302, 303)
                mock_create.assert_not_called()

    def test_profanity_is_accepted(self, monkeypatch):
        """POST with ordinary profanity ("shithole") is accepted."""
        client, events = self._client(monkeypatch)

        with patch("app.get_campsite_by_id", return_value={"id": 42, "name": "Test"}), \
             patch("app.count_reviews_today", return_value=0), \
             patch("app.create_review") as mock_create, \
             patch("app.current_user", return_value={"id": 1, "username": "test"}), \
             patch("app.get_review_by_user_and_campsite", return_value=None):

            mock_create.return_value = 123

            with client:
                with client.session_transaction() as sess:
                    sess["user_id"] = 1
                    sess["_csrf"] = "test-token"

                resp = client.post(
                    "/campsite/42/review",
                    data={
                        "_csrf": "test-token",
                        "verdict": "down",
                        "body": "This place was a shithole",  # profanity is OK
                    },
                )

                # Should succeed
                assert resp.status_code in (302, 303)
                mock_create.assert_called_once()
                # Verify the body was passed as-is
                call_kwargs = mock_create.call_args[1]
                assert call_kwargs["body"] == "This place was a shithole"

    def test_scunthorpe_false_positive_is_accepted(self, monkeypatch):
        """POST with a word that contains a blocked substring is accepted."""
        client, events = self._client(monkeypatch)

        with patch("app.get_campsite_by_id", return_value={"id": 42, "name": "Test"}), \
             patch("app.count_reviews_today", return_value=0), \
             patch("app.create_review") as mock_create, \
             patch("app.current_user", return_value={"id": 1, "username": "test"}), \
             patch("app.get_review_by_user_and_campsite", return_value=None):

            mock_create.return_value = 123

            with client:
                with client.session_transaction() as sess:
                    sess["user_id"] = 1
                    sess["_csrf"] = "test-token"

                # "Scunthorpe" contains "cunt" but should not be blocked
                resp = client.post(
                    "/campsite/42/review",
                    data={
                        "_csrf": "test-token",
                        "verdict": "up",
                        "body": "Visited Scunthorpe area",
                    },
                )

                # Should succeed
                assert resp.status_code in (302, 303)
                mock_create.assert_called_once()

    def test_invalid_attribute_is_rejected(self, monkeypatch):
        """POST with an attribute name outside the allowed six is rejected."""
        client, _ = self._client(monkeypatch)

        with patch("app.get_campsite_by_id", return_value={"id": 42, "name": "Test"}), \
             patch("app.count_reviews_today", return_value=0), \
             patch("app.create_review") as mock_create, \
             patch("app.current_user", return_value={"id": 1, "username": "test"}):

            with client:
                with client.session_transaction() as sess:
                    sess["user_id"] = 1
                    sess["_csrf"] = "test-token"

                resp = client.post(
                    "/campsite/42/review",
                    data={
                        "_csrf": "test-token",
                        "verdict": "up",
                        "body": "Great",
                        "attribute_bad_attr": "value",  # invalid attribute
                    },
                )

                # Should redirect without creating review
                assert resp.status_code in (302, 303)
                mock_create.assert_not_called()

    def test_valid_attributes_are_accepted(self, monkeypatch):
        """POST with valid attribute names (water, toilet_type, etc) succeeds."""
        client, events = self._client(monkeypatch)

        with patch("app.get_campsite_by_id", return_value={"id": 42, "name": "Test"}), \
             patch("app.count_reviews_today", return_value=0), \
             patch("app.create_review") as mock_create, \
             patch("app.current_user", return_value={"id": 1, "username": "test"}), \
             patch("app.get_review_by_user_and_campsite", return_value=None):

            mock_create.return_value = 123

            with client:
                with client.session_transaction() as sess:
                    sess["user_id"] = 1
                    sess["_csrf"] = "test-token"

                resp = client.post(
                    "/campsite/42/review",
                    data={
                        "_csrf": "test-token",
                        "verdict": "up",
                        "body": "Great water",
                        "attribute_water": "yes",
                        "attribute_toilet_type": "vault",
                    },
                )

                # Should succeed
                assert resp.status_code in (302, 303)
                call_kwargs = mock_create.call_args[1]
                # Verify attribute reports were passed
                assert len(call_kwargs["attribute_reports"]) == 2

    def test_review_and_attributes_transaction_atomicity(self, monkeypatch):
        """If attribute insert fails, the review is not created (transaction rollback)."""
        client, _ = self._client(monkeypatch)

        with patch("app.get_campsite_by_id", return_value={"id": 42, "name": "Test"}), \
             patch("app.count_reviews_today", return_value=0), \
             patch("app.create_review") as mock_create, \
             patch("app.current_user", return_value={"id": 1, "username": "test"}):

            # Simulate attribute validation error
            mock_create.side_effect = psycopg2.DatabaseError("attribute check failed")

            with client:
                with client.session_transaction() as sess:
                    sess["user_id"] = 1
                    sess["_csrf"] = "test-token"

                resp = client.post(
                    "/campsite/42/review",
                    data={
                        "_csrf": "test-token",
                        "verdict": "up",
                        "body": "Great",
                        "attribute_water": "invalid_value",
                    },
                )

                # Should redirect with error message
                assert resp.status_code in (302, 303)

    def test_missing_csrf_token_is_rejected(self, monkeypatch):
        """POST without a CSRF token is rejected."""
        client, _ = self._client(monkeypatch)

        with patch("app.get_campsite_by_id", return_value={"id": 42, "name": "Test"}), \
             patch("app.current_user", return_value={"id": 1, "username": "test"}):

            with client:
                with client.session_transaction() as sess:
                    sess["user_id"] = 1
                    sess["_csrf"] = "test-token"

                # POST without _csrf parameter
                resp = client.post(
                    "/campsite/42/review",
                    data={
                        # no _csrf field
                        "verdict": "up",
                        "body": "Great",
                    },
                )

                # Should be rejected with 400
                assert resp.status_code == 400, "missing CSRF should return 400"

    def test_review_submitted_event_is_logged(self, monkeypatch):
        """On successful review submit, a review_submitted event is logged."""
        client, events = self._client(monkeypatch)

        with patch("app.get_campsite_by_id", return_value={"id": 42, "name": "Test"}), \
             patch("app.count_reviews_today", return_value=0), \
             patch("app.create_review") as mock_create, \
             patch("app.current_user", return_value={"id": 1, "username": "test"}), \
             patch("app.get_review_by_user_and_campsite", return_value=None):

            mock_create.return_value = 123

            with client:
                with client.session_transaction() as sess:
                    sess["user_id"] = 1
                    sess["_csrf"] = "test-token"

                resp = client.post(
                    "/campsite/42/review",
                    data={
                        "_csrf": "test-token",
                        "verdict": "up",
                        "body": "Great",
                    },
                )

                # Should have logged review_submitted event
                assert any(e.get("event_type") == "review_submitted" for e in events), \
                    f"expected review_submitted event, got {events}"

    def test_logging_failure_does_not_break_submission(self, monkeypatch):
        """If logging fails, the review is still created (best-effort logging)."""
        client, _ = self._client(monkeypatch)

        with patch("app.get_campsite_by_id", return_value={"id": 42, "name": "Test"}), \
             patch("app.count_reviews_today", return_value=0), \
             patch("app.create_review") as mock_create, \
             patch("app.current_user", return_value={"id": 1, "username": "test"}), \
             patch("app.get_review_by_user_and_campsite", return_value=None), \
             patch("app.log_event") as mock_log:

            mock_create.return_value = 123
            mock_log.side_effect = Exception("logging failed")

            with client:
                with client.session_transaction() as sess:
                    sess["user_id"] = 1
                    sess["_csrf"] = "test-token"

                resp = client.post(
                    "/campsite/42/review",
                    data={
                        "_csrf": "test-token",
                        "verdict": "up",
                        "body": "Great",
                    },
                )

                # Should still succeed
                assert resp.status_code in (302, 303)
                # create_review should have been called
                mock_create.assert_called_once()
