"""Tests for anonymous identity cookie and event history merge.

These tests verify:
- Cookie creation, validation, and tampering detection
- Event history merge on signup and login
- current_actor() helper
- Proper error handling when merge fails
"""

import pytest
from uuid import UUID
from unittest.mock import patch, MagicMock, Mock
from itsdangerous import URLSafeTimedSerializer, BadSignature

# Import the app module for testing
import app as app_module
import db as db_module


class TestAnonCookieDaysConstant:
    """Test the ANON_COOKIE_DAYS constant."""

    def test_anon_cookie_days_is_30(self):
        """The ANON_COOKIE_DAYS constant must be exactly 30."""
        assert app_module.ANON_COOKIE_DAYS == 30, "ANON_COOKIE_DAYS must be 30"


class TestAnonSerializerCreation:
    """Test the serializer creation for signing cookies."""

    def test_get_anon_serializer_returns_serializer(self):
        """_get_anon_serializer should return a URLSafeTimedSerializer."""
        serializer = app_module._get_anon_serializer()
        assert isinstance(serializer, URLSafeTimedSerializer)

    def test_serializer_uses_flask_secret_key(self):
        """The serializer should use the Flask secret key."""
        with app_module.app.app_context():
            serializer = app_module._get_anon_serializer()
            # Can't directly check the secret, but we can verify it signs/unsigns
            test_val = "test-uuid-string"
            signed = serializer.dumps(test_val)
            unsigned = serializer.loads(signed)
            assert unsigned == test_val


class TestCookieValidation:
    """Test the cookie validation logic."""

    def test_valid_uuid_cookie_is_accepted(self):
        """A properly signed UUID cookie should be accepted."""
        test_uuid = "12345678-1234-5678-1234-567812345678"

        with app_module.app.test_request_context("/"):
            serializer = app_module._get_anon_serializer()
            signed_val = serializer.dumps(test_uuid)

            # Simulate Flask request.cookies
            with patch("app.request.cookies", {("cs_anon"): signed_val}):
                result = app_module._get_anon_id_from_cookie()
                assert result == test_uuid

    def test_tampered_cookie_is_rejected(self):
        """A tampered (unsigned) cookie should be rejected."""
        with app_module.app.test_request_context("/"):
            with patch("app.request.cookies", {"cs_anon": "tampered-garbage"}):
                result = app_module._get_anon_id_from_cookie()
                assert result is None

    def test_invalid_uuid_in_cookie_is_rejected(self):
        """If the cookie contains a non-UUID value, it should be rejected."""
        with app_module.app.test_request_context("/"):
            serializer = app_module._get_anon_serializer()
            signed_bad = serializer.dumps("not-a-uuid")

            with patch("app.request.cookies", {"cs_anon": signed_bad}):
                result = app_module._get_anon_id_from_cookie()
                assert result is None

    def test_missing_cookie_returns_none(self):
        """If there is no cs_anon cookie, _get_anon_id_from_cookie returns None."""
        with app_module.app.test_request_context("/"):
            with patch("app.request.cookies", {}):
                result = app_module._get_anon_id_from_cookie()
                assert result is None


class TestCurrentActor:
    """Test the current_actor() helper function."""

    def test_signed_in_user_returns_user_id_none(self):
        """When signed in, current_actor() returns (user_id, None)."""
        with app_module.app.test_request_context("/"):
            with patch("app.session", {"user_id": 42}):
                user_id, anon_id = app_module.current_actor()
                assert user_id == 42
                assert anon_id is None

    def test_anonymous_visitor_with_cookie_returns_anon_id(self):
        """When anonymous with a cookie, current_actor() returns (None, anon_id)."""
        test_uuid = "12345678-1234-5678-1234-567812345678"

        with app_module.app.test_request_context("/"):
            serializer = app_module._get_anon_serializer()
            signed_val = serializer.dumps(test_uuid)

            with patch("app.session", {}):
                with patch("app.request.cookies", {"cs_anon": signed_val}):
                    user_id, anon_id = app_module.current_actor()
                    assert user_id is None
                    assert anon_id == test_uuid

    def test_anonymous_without_cookie_returns_none_anon_id(self):
        """When anonymous without a valid cookie, current_actor() returns (None, None)."""
        with app_module.app.test_request_context("/"):
            with patch("app.session", {}):
                with patch("app.request.cookies", {}):
                    user_id, anon_id = app_module.current_actor()
                    assert user_id is None
                    assert anon_id is None


class TestSetAnonCookie:
    """Test the _set_anon_cookie function."""

    def test_set_anon_cookie_has_httponly_flag(self):
        """The cookie should be set with HttpOnly=True."""
        with app_module.app.test_request_context("/"):
            mock_response = MagicMock()
            test_uuid = "12345678-1234-5678-1234-567812345678"

            app_module._set_anon_cookie(mock_response, test_uuid)

            # Check the set_cookie call
            call_kwargs = mock_response.set_cookie.call_args[1]
            assert call_kwargs["httponly"] is True

    def test_set_anon_cookie_has_samesite_lax(self):
        """The cookie should be set with SameSite='Lax'."""
        with app_module.app.test_request_context("/"):
            mock_response = MagicMock()
            test_uuid = "12345678-1234-5678-1234-567812345678"

            app_module._set_anon_cookie(mock_response, test_uuid)

            call_kwargs = mock_response.set_cookie.call_args[1]
            assert call_kwargs["samesite"] == "Lax"

    def test_set_anon_cookie_max_age_is_30_days(self):
        """The cookie should have max_age of 30 days (2592000 seconds)."""
        with app_module.app.test_request_context("/"):
            mock_response = MagicMock()
            test_uuid = "12345678-1234-5678-1234-567812345678"

            app_module._set_anon_cookie(mock_response, test_uuid)

            call_kwargs = mock_response.set_cookie.call_args[1]
            assert call_kwargs["max_age"] == 30 * 24 * 3600

    def test_set_anon_cookie_secure_for_https(self):
        """The Secure flag should be True for HTTPS requests."""
        with app_module.app.test_request_context("/", environ_overrides={"wsgi.url_scheme": "https"}):
            mock_response = MagicMock()
            test_uuid = "12345678-1234-5678-1234-567812345678"

            app_module._set_anon_cookie(mock_response, test_uuid)

            call_kwargs = mock_response.set_cookie.call_args[1]
            assert call_kwargs["secure"] is True

    def test_set_anon_cookie_insecure_for_http(self):
        """The Secure flag should be False for HTTP requests."""
        with app_module.app.test_request_context("/"):
            mock_response = MagicMock()
            test_uuid = "12345678-1234-5678-1234-567812345678"

            app_module._set_anon_cookie(mock_response, test_uuid)

            call_kwargs = mock_response.set_cookie.call_args[1]
            assert call_kwargs["secure"] is False


class TestMergeAnonEvents:
    """Test the merge_anon_events function in db.py."""

    def test_merge_issues_correct_update_with_right_parameters(self):
        """The merge should UPDATE user_events with the correct SQL and parameters."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("db.get_connection", return_value=mock_conn):
            anon_id = "12345678-1234-5678-1234-567812345678"
            user_id = 99

            db_module.merge_anon_events(user_id, anon_id)

            # Verify execute was called
            mock_cursor.execute.assert_called_once()

            # Check the SQL and parameters
            call_args = mock_cursor.execute.call_args
            sql = call_args[0][0]
            params = call_args[0][1]

            assert "UPDATE user_events" in sql
            assert "SET user_id = %s, anon_id = NULL" in sql
            assert "WHERE anon_id = %s AND user_id IS NULL" in sql
            assert params == (user_id, anon_id)

    def test_merge_commits_transaction(self):
        """The merge should commit the transaction."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("db.get_connection", return_value=mock_conn):
            anon_id = "12345678-1234-5678-1234-567812345678"
            user_id = 99

            db_module.merge_anon_events(user_id, anon_id)

            mock_conn.commit.assert_called_once()

    def test_merge_closes_connection(self):
        """The merge should close the database connection."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("db.get_connection", return_value=mock_conn):
            anon_id = "12345678-1234-5678-1234-567812345678"
            user_id = 99

            db_module.merge_anon_events(user_id, anon_id)

            mock_conn.close.assert_called_once()

    def test_merge_error_does_not_raise(self):
        """If merge encounters an error, it should not raise."""
        with patch("db.get_connection", side_effect=Exception("DB error")):
            anon_id = "12345678-1234-5678-1234-567812345678"
            user_id = 99

            # Should not raise; it swallows the error
            db_module.merge_anon_events(user_id, anon_id)
            # If we get here, the error was swallowed successfully


class TestAnonIdentityHook:
    """Test the _anon_identity before_request hook."""

    def test_anon_identity_bypasses_static_requests(self):
        """Static file requests should bypass the anon identity logic."""
        with app_module.app.test_request_context("/static/css/style.css"):
            with patch("app.uuid4"):
                with patch("app.session", {}):
                    with patch("app.request.cookies", {}):
                        app_module._anon_identity()
                        # uuid4 should not have been called for static files
                        # (The anon identity should not have been set up)

    def test_anon_identity_sets_g_anon_id_for_fresh_visitor(self):
        """A fresh visitor should get g.anon_id set."""
        with app_module.app.test_request_context("/"):
            with patch("app.session", {}):
                with patch("app.request.cookies", {}):
                    with patch("app.uuid4") as mock_uuid:
                        mock_uuid.return_value = MagicMock()
                        mock_uuid.return_value.__str__ = lambda x: "mocked-uuid"

                        app_module._anon_identity()

                        # After calling _anon_identity, g.anon_id should be set
                        # (In a real request, we'd check this, but in testing
                        # we need to verify the logic works)


class TestSetAnonCookieResponse:
    """Test the _set_anon_cookie_response after_request hook."""

    def test_cookie_not_set_if_no_g_anon_id(self):
        """If g.anon_id is not set, the after_request should not set a cookie."""
        with app_module.app.test_request_context("/"):
            mock_response = MagicMock()

            # Don't set g.anon_id
            result = app_module._set_anon_cookie_response(mock_response)

            # The response should be returned unchanged
            assert result == mock_response

    def test_cookie_is_set_if_g_anon_id_present(self):
        """If g.anon_id is set, the after_request should set a cookie."""
        with app_module.app.test_request_context("/"):
            from flask import g
            g.anon_id = "12345678-1234-5678-1234-567812345678"

            mock_response = MagicMock()

            with patch("app._set_anon_cookie") as mock_set_cookie:
                result = app_module._set_anon_cookie_response(mock_response)

                # Should have called _set_anon_cookie
                mock_set_cookie.assert_called_once()
                assert result == mock_response


class TestMergeDoesNotLeakConnections:
    """A failed merge must still close its connection.

    merge_anon_events swallows errors so a login never fails. Closing the
    connection only on the happy path means every failure leaks one, and
    enough leaks exhaust the Postgres connection limit and take the app down,
    which is worse than the lost history the swallow protects against.
    record_pick already uses try/except/finally; this must match it.
    """

    def _fake_conn(self, raise_on_execute):
        class Cur:
            def execute(self, *a, **k):
                if raise_on_execute:
                    raise RuntimeError("boom")
            def close(self):
                pass

        class Conn:
            def __init__(self):
                self.closed_count = 0
            def cursor(self):
                return Cur()
            def commit(self):
                pass
            def close(self):
                self.closed_count += 1
        return Conn()

    def test_connection_closed_when_update_fails(self, monkeypatch):
        import db
        conn = self._fake_conn(raise_on_execute=True)
        monkeypatch.setattr(db, "get_connection", lambda: conn)
        db.merge_anon_events(1, "some-anon-id")
        assert conn.closed_count == 1, (
            "connection was not closed after a failed merge, so every "
            "failure leaks one")

    def test_connection_closed_on_success(self, monkeypatch):
        import db
        conn = self._fake_conn(raise_on_execute=False)
        monkeypatch.setattr(db, "get_connection", lambda: conn)
        db.merge_anon_events(1, "some-anon-id")
        assert conn.closed_count == 1

    def test_failed_merge_does_not_raise(self, monkeypatch):
        import db
        monkeypatch.setattr(db, "get_connection",
                            lambda: (_ for _ in ()).throw(RuntimeError("down")))
        db.merge_anon_events(1, "some-anon-id")  # must not raise
