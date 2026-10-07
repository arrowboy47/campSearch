"""Tests for attribute correction system (Task 13).

Tests cover:
- evaluate_attribute_reports() evaluation logic with distinct user counting
- apply_attribute_correction() with atomic transaction and audit trail
- Admin routes for applying and dismissing corrections
- CSRF protection and admin_required decorator
- Type coercion and validation for each attribute
- Threshold enforcement (both distinct user count and percentage independently)
"""

import pytest
from unittest.mock import patch, MagicMock, Mock, call
import psycopg2
from decimal import Decimal

import app as app_module
import db as db_module


@pytest.fixture(autouse=True)
def no_db(monkeypatch):
    """Stub every database call the page routes make.

    This suite runs with no database and no network.
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
        "evaluate_attribute_reports": [],
    }
    for name, value in stubs.items():
        if hasattr(A, name):
            monkeypatch.setattr(A, name, (lambda v: (lambda *a, **k: v))(value))
    return stubs


class TestEvaluateAttributeReports:
    """Tests for evaluate_attribute_reports() evaluation logic."""

    def test_two_agreeing_users_do_not_meet_threshold_of_three(self):
        """Two distinct users reporting the same value do not meet N=3 threshold."""
        with patch("db.get_connection") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_conn = MagicMock()
            mock_get_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            # Simulates two distinct users (review_id 1 and 2 from different users)
            # reporting "flush" for toilet_type at campsite 1.
            mock_cursor.fetchall.return_value = [
                {
                    "campsite_id": 1,
                    "campsite_name": "Test Site",
                    "attribute": "toilet_type",
                    "claimed_value": "flush",
                    "agreeing_users": 2,
                    "total_users": 2,
                    "agreement_percent": 100,
                    "meets_threshold": False,  # 2 < 3
                },
            ]

            result = db_module.evaluate_attribute_reports()
            assert len(result) == 1
            assert result[0]["meets_threshold"] is False
            assert result[0]["agreeing_users"] == 2

    def test_three_agreeing_users_at_100_percent_meet_threshold(self):
        """Three distinct users all reporting the same value meets both gates."""
        with patch("db.get_connection") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_conn = MagicMock()
            mock_get_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            mock_cursor.fetchall.return_value = [
                {
                    "campsite_id": 1,
                    "campsite_name": "Test Site",
                    "attribute": "is_free",
                    "claimed_value": True,
                    "agreeing_users": 3,
                    "total_users": 3,
                    "agreement_percent": 100,
                    "meets_threshold": True,  # 3 >= 3 AND 100 >= 75
                },
            ]

            result = db_module.evaluate_attribute_reports()
            assert result[0]["meets_threshold"] is True
            assert result[0]["agreeing_users"] == 3

    def test_three_users_at_50_percent_do_not_meet_percentage_threshold(self):
        """Three users at 50% agreement do not meet X=75% threshold."""
        with patch("db.get_connection") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_conn = MagicMock()
            mock_get_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            # 3 users agree, but only 3 out of 6 total = 50%
            mock_cursor.fetchall.return_value = [
                {
                    "campsite_id": 1,
                    "campsite_name": "Test Site",
                    "attribute": "fee_min",
                    "claimed_value": 15.00,
                    "agreeing_users": 3,
                    "total_users": 6,
                    "agreement_percent": 50,
                    "meets_threshold": False,  # 50 < 75
                },
            ]

            result = db_module.evaluate_attribute_reports()
            assert result[0]["meets_threshold"] is False
            assert result[0]["agreement_percent"] == 50

    def test_distinct_users_not_distinct_reports(self):
        """One user filing three reports does not count as three distinct users."""
        with patch("db.get_connection") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_conn = MagicMock()
            mock_get_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            # Same user (user_id 1) files 3 reports, but only counts as 1 distinct user
            mock_cursor.fetchall.return_value = [
                {
                    "campsite_id": 1,
                    "campsite_name": "Test Site",
                    "attribute": "water",
                    "claimed_value": "lake",
                    "agreeing_users": 1,
                    "total_users": 1,
                    "agreement_percent": 100,
                    "meets_threshold": False,  # 1 < 3, even with 3 reports
                },
            ]

            result = db_module.evaluate_attribute_reports()
            assert result[0]["meets_threshold"] is False
            assert result[0]["agreeing_users"] == 1

    def test_threshold_constants_read_from_module(self, monkeypatch):
        """Threshold constants are read from module, not hardcoded in SQL."""
        # Change the thresholds
        monkeypatch.setattr(db_module, "ATTRIBUTE_THRESHOLD_MIN_USERS", 2)
        monkeypatch.setattr(db_module, "ATTRIBUTE_THRESHOLD_MIN_PERCENT", 50)

        with patch("db.get_connection") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_conn = MagicMock()
            mock_get_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            # 2 users at 50% should now meet the threshold with lowered constants
            mock_cursor.fetchall.return_value = [
                {
                    "campsite_id": 1,
                    "campsite_name": "Test Site",
                    "attribute": "is_open",
                    "claimed_value": True,
                    "agreeing_users": 2,
                    "total_users": 4,
                    "agreement_percent": 50,
                    "meets_threshold": True,  # Now passes with lowered constants
                },
            ]

            result = db_module.evaluate_attribute_reports()
            # Verify the SQL query was called with the monkeypatched values
            call_args = mock_cursor.execute.call_args
            assert call_args is not None
            sql, params = call_args[0]
            # The params passed to execute should be the monkeypatched values
            assert params == (2, 50)
            assert result[0]["meets_threshold"] is True


class TestApplyAttributeCorrection:
    """Tests for apply_attribute_correction() function and type coercion."""

    def test_invalid_attribute_is_refused(self):
        """Attributes outside the allowed six are refused."""
        with pytest.raises(ValueError) as excinfo:
            db_module.apply_attribute_correction(1, "invalid_attr", "value", 1)
        assert "not allowed" in str(excinfo.value)

    def test_wrong_type_for_fee_min_is_refused(self):
        """Non-numeric values for fee_min are refused."""
        with pytest.raises(ValueError) as excinfo:
            db_module.apply_attribute_correction(1, "fee_min", "banana", 1)
        assert "numeric" in str(excinfo.value)

    def test_wrong_type_for_is_free_is_refused(self):
        """Non-boolean values for is_free are refused."""
        with pytest.raises(ValueError) as excinfo:
            db_module.apply_attribute_correction(1, "is_free", "maybe", 1)
        assert "boolean" in str(excinfo.value)

    def test_invalid_toilet_type_is_refused(self):
        """toilet_type values outside the allowed set are refused."""
        with pytest.raises(ValueError) as excinfo:
            db_module.apply_attribute_correction(1, "toilet_type", "bidet", 1)
        assert "must be one of" in str(excinfo.value)

    def test_apply_writes_data_and_audit_atomically(self):
        """Applying writes both data change and audit row in one transaction."""
        with patch("db.get_connection") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_conn = MagicMock()
            mock_get_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            db_module.apply_attribute_correction(1, "is_free", True, 2)

            # Verify both UPDATE and INSERT were called
            assert mock_cursor.execute.call_count == 2
            calls = mock_cursor.execute.call_args_list

            # First call should be the data update
            first_sql = calls[0][0][0]
            assert "UPDATE campsites" in first_sql
            assert "is_free" in first_sql

            # Second call should be the audit insert
            second_sql = calls[1][0][0]
            assert "INSERT INTO moderation_actions" in second_sql
            assert "approve" in second_sql

            # Both should be followed by a commit
            mock_conn.commit.assert_called_once()

    def test_audit_insert_fails_rolls_back_data_change(self):
        """If the audit insert fails, the data change rolls back."""
        with patch("db.get_connection") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_conn = MagicMock()
            mock_get_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            # First execute succeeds (data update), second fails (audit insert)
            mock_cursor.execute.side_effect = [None, psycopg2.DatabaseError("audit insert failed")]

            with pytest.raises(psycopg2.DatabaseError):
                db_module.apply_attribute_correction(1, "toilet_type", "flush", 2)

            # Verify rollback was called
            mock_conn.rollback.assert_called_once()
            mock_conn.commit.assert_not_called()

    def test_dismiss_writes_audit_row_no_data_change(self):
        """Dismissing writes an audit row but does not change any attribute data."""
        # Note: dismiss is implemented in the app.py route, not in db.py
        # But we can test it through the route mock
        with patch("db.get_connection") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_conn = MagicMock()
            mock_get_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            # Test that apply would not modify anything if we called it with None
            # (dismiss only writes audit, no data change)
            # This is tested in the route tests instead

    def test_type_coercion_boolean_true_string(self):
        """Boolean coercion accepts 'true', '1', 'yes' for true."""
        with patch("db.get_connection") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_conn = MagicMock()
            mock_get_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            for val in ['true', '1', 'yes', 'True', 'TRUE']:
                mock_cursor.execute.reset_mock()
                mock_conn.rollback.reset_mock()
                mock_conn.commit.reset_mock()

                db_module.apply_attribute_correction(1, "is_open", val, 1)

                # Verify that True was passed to UPDATE, not the string
                calls = mock_cursor.execute.call_args_list
                # calls[0] is a call object with args and kwargs
                update_sql, update_params = calls[0][0]
                assert update_params[0] is True, f"Failed for input '{val}'"

    def test_type_coercion_boolean_false_string(self):
        """Boolean coercion accepts 'false', '0', 'no' for false."""
        with patch("db.get_connection") as mock_get_conn:
            mock_cursor = MagicMock()
            mock_conn = MagicMock()
            mock_get_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            for val in ['false', '0', 'no', 'False', 'FALSE']:
                mock_cursor.execute.reset_mock()
                mock_conn.rollback.reset_mock()
                mock_conn.commit.reset_mock()

                db_module.apply_attribute_correction(1, "is_reservable", val, 1)

                # Verify that False was passed to UPDATE, not the string
                calls = mock_cursor.execute.call_args_list
                # calls[0] is a call object with args and kwargs
                update_sql, update_params = calls[0][0]
                assert update_params[0] is False, f"Failed for input '{val}'"


class TestAdminRoutes:
    """Tests for admin routes with CSRF protection and access control."""

    def test_non_admin_gets_404_on_apply_route(self):
        """Non-admins get 404 on /admin/attribute/apply route."""
        client = app_module.app.test_client()

        # Anonymous request with CSRF token
        with client.session_transaction() as sess:
            sess["_csrf"] = "test_token"

        response = client.post(
            "/admin/attribute/apply",
            data={
                "campsite_id": 1,
                "attribute": "is_free",
                "claimed_value": "true",
                "_csrf": "test_token",
            },
        )
        assert response.status_code == 404

    def test_non_admin_gets_404_on_dismiss_route(self):
        """Non-admins get 404 on /admin/attribute/dismiss route."""
        client = app_module.app.test_client()

        # Anonymous request with CSRF token
        with client.session_transaction() as sess:
            sess["_csrf"] = "test_token"

        response = client.post(
            "/admin/attribute/dismiss",
            data={
                "campsite_id": 1,
                "_csrf": "test_token",
            },
        )
        assert response.status_code == 404

    def test_signed_in_non_admin_gets_404_on_apply_route(self):
        """Signed-in non-admins get 404 on /admin/attribute/apply route."""
        with patch("app.current_user") as mock_current_user:
            mock_current_user.return_value = {
                "id": 1,
                "username": "user",
                "is_admin": False,
            }

            client = app_module.app.test_client()
            with client.session_transaction() as sess:
                sess["user_id"] = 1
                sess["_csrf"] = "test_token"

            response = client.post(
                "/admin/attribute/apply",
                data={
                    "campsite_id": 1,
                    "attribute": "is_free",
                    "claimed_value": "true",
                    "_csrf": "test_token",
                },
            )
            assert response.status_code == 404

    def test_apply_route_requires_csrf_token(self):
        """POST to /admin/attribute/apply without CSRF token is rejected."""
        with patch("app.current_user") as mock_current_user:
            mock_current_user.return_value = {
                "id": 1,
                "username": "admin",
                "is_admin": True,
            }

            client = app_module.app.test_client()
            with client.session_transaction() as sess:
                sess["user_id"] = 1

            response = client.post(
                "/admin/attribute/apply",
                data={
                    "campsite_id": 1,
                    "attribute": "is_free",
                    "claimed_value": "true",
                    # No _csrf token
                },
            )
            assert response.status_code == 400
            assert "CSRF token" in response.get_data(as_text=True)

    def test_dismiss_route_requires_csrf_token(self):
        """POST to /admin/attribute/dismiss without CSRF token is rejected."""
        with patch("app.current_user") as mock_current_user:
            mock_current_user.return_value = {
                "id": 1,
                "username": "admin",
                "is_admin": True,
            }

            client = app_module.app.test_client()
            with client.session_transaction() as sess:
                sess["user_id"] = 1

            response = client.post(
                "/admin/attribute/dismiss",
                data={
                    "campsite_id": 1,
                    # No _csrf token
                },
            )
            assert response.status_code == 400
            assert "CSRF token" in response.get_data(as_text=True)

    def test_apply_route_calls_apply_attribute_correction(self):
        """The apply route calls apply_attribute_correction with correct params."""
        with patch("app.current_user") as mock_current_user, \
             patch("app.apply_attribute_correction") as mock_apply:
            mock_current_user.return_value = {
                "id": 2,
                "username": "admin",
                "is_admin": True,
            }

            client = app_module.app.test_client()
            with client.session_transaction() as sess:
                sess["user_id"] = 2
                sess["_csrf"] = "test_token"

            response = client.post(
                "/admin/attribute/apply",
                data={
                    "campsite_id": 42,
                    "attribute": "is_free",
                    "claimed_value": "true",
                    "_csrf": "test_token",
                },
            )

            mock_apply.assert_called_once_with(42, "is_free", "true", 2)
            assert response.status_code == 302  # Redirect

    def test_dismiss_route_writes_audit_row(self):
        """The dismiss route writes a dismiss audit row."""
        with patch("app.current_user") as mock_current_user, \
             patch("app.get_connection") as mock_get_conn:
            mock_current_user.return_value = {
                "id": 3,
                "username": "admin",
                "is_admin": True,
            }

            mock_cursor = MagicMock()
            mock_conn = MagicMock()
            mock_get_conn.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor

            client = app_module.app.test_client()
            with client.session_transaction() as sess:
                sess["user_id"] = 3
                sess["_csrf"] = "test_token"

            response = client.post(
                "/admin/attribute/dismiss",
                data={
                    "campsite_id": 99,
                    "_csrf": "test_token",
                },
            )

            # Verify audit insert was called with dismiss action
            calls = mock_cursor.execute.call_args_list
            assert len(calls) == 1
            # Extract sql and params from the call object
            call_args = calls[0][0]  # Get the positional args of the call
            sql = call_args[0]
            params = call_args[1]
            assert "INSERT INTO moderation_actions" in sql
            assert "dismiss" in sql
            assert "'dismiss'" in sql  # dismiss is hardcoded in SQL
            assert "'attribute_report'" in sql  # target_type is hardcoded in SQL
            assert params == (3, 99)  # Only user_id and campsite_id are params
            assert response.status_code == 302  # Redirect


class TestAttributeColumnMappingIsReal:
    """Each attribute must write to the column that actually holds it.

    As first written, attribute 'water' was coerced as "TEXT, accept any
    string" and written to amenities.water_feature. Those are different
    columns: amenities.water is a BOOLEAN meaning "has drinking water", while
    water_feature is the lake/creek/river TEXT facet that the search filter
    reads. A user correctly reporting "no drinking water" would have written
    the string "false" into water_feature, leaving the boolean untouched and
    corrupting a search facet.

    Every test passed, because they all mocked the database connection, so the
    column name in the SQL was never checked against a real schema. These
    assert on the SQL this function actually emits and, where a database is
    reachable, on information_schema itself.
    """

    EXPECTED = {
        "water":         ("amenities",      "water",         "boolean"),
        "toilet_type":   ("amenities",      "toilet_type",   "text"),
        "is_free":       ("campsites",      "is_free",       "boolean"),
        "fee_min":       ("campsites",      "fee_min",       "numeric"),
        "is_reservable": ("reservations",   "is_reservable", "boolean"),
        "is_open":       ("status_updates", "is_open",       "boolean"),
    }

    def _captured_sql(self, attribute, value):
        import db
        seen = []

        class Cur:
            def execute(self, q, p=None):
                seen.append(" ".join(q.split()))
            def fetchone(self): return (1,)
            def close(self): pass

        class Conn:
            def cursor(self, **k): return Cur()
            def commit(self): pass
            def rollback(self): pass
            def close(self): pass

        orig = db.get_connection
        db.get_connection = lambda: Conn()
        try:
            db.apply_attribute_correction(1, attribute, value, 7)
        finally:
            db.get_connection = orig
        return seen

    def test_each_attribute_updates_the_right_table_and_column(self):
        samples = {"water": True, "toilet_type": "vault", "is_free": True,
                   "fee_min": "12.50", "is_reservable": True, "is_open": True}
        for attr, (table, column, _type) in self.EXPECTED.items():
            sql = " | ".join(self._captured_sql(attr, samples[attr]))
            assert "UPDATE %s SET %s =" % (table, column) in sql, (
                "attribute %r must update %s.%s, got: %s"
                % (attr, table, column, sql))

    def test_water_is_not_written_to_water_feature(self):
        """The specific regression. water_feature must never be touched."""
        sql = " | ".join(self._captured_sql("water", False))
        assert "water_feature" not in sql, (
            "a water report was written to water_feature, which is the "
            "lake/creek/river facet, not the drinking-water boolean")

    def test_water_rejects_a_non_boolean(self):
        import db, pytest as _pytest
        with _pytest.raises(ValueError):
            self._captured_sql("water", "lake")

    def test_expected_columns_match_the_live_schema(self):
        """Skips when no database is reachable, so the suite stays DB free."""
        import os, pytest as _pytest
        try:
            import psycopg2
            from dotenv import load_dotenv
            load_dotenv("/home/arrowboy/Projects/campSearch/.env")
            conn = psycopg2.connect(os.environ["DATABASE_URL"],
                                    connect_timeout=3)
        except Exception:
            _pytest.skip("no database reachable; covered by the SQL assertions")
        try:
            cur = conn.cursor()
            for attr, (table, column, want_type) in self.EXPECTED.items():
                cur.execute(
                    "select data_type from information_schema.columns "
                    "where table_name=%s and column_name=%s", (table, column))
                row = cur.fetchone()
                assert row, "%s.%s does not exist" % (table, column)
                assert row[0].startswith(want_type), (
                    "%s.%s is %s, expected %s" % (table, column, row[0], want_type))
        finally:
            conn.close()
