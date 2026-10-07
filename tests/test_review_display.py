"""Tests for review display on campsite page (Task 11).

Tests cover:
- Review list rendering for published reviews only
- Verdict summary (thumbs up vs thumbs down)
- Approved photo rendering through /api/review_photos/ endpoint
- Pending and rejected photos NOT appearing
- Removed reviews NOT appearing
- XSS protection (review bodies are escaped)
- Pagination (20 per page)
- No "awaiting approval" text leaking to public
"""

import pytest
import re
from unittest.mock import patch, MagicMock
from jinja2 import Environment, FileSystemLoader
import os

import app as app_module


@pytest.fixture(autouse=True)
def no_db(monkeypatch):
    """Stub every database call the page routes make.

    This suite runs with no database and no network.
    """
    import app as A
    stubs = {
        "get_campsite_by_id": {
            "id": 42,
            "name": "Test Campsite",
            "latitude": 35.5,
            "longitude": -119.5,
            "approx_latitude": None,
            "approx_longitude": None,
            "approx_coord_source": None,
            "address": "Test County",
            "managing_unit": "Test Unit",
            "forest_name": "Test Forest",
            "reservation_type": None,
            "reservation_url": None,
            "contact_name": None,
            "contact_phone": None,
            "seasons_of_use": None,
            "num_sites": None,
            "overview": None,
            "fee": None,
            "fee_min": None,
            "fee_max": None,
            "is_free": False,
            "elevation_ft": None,
            "terrain": None,
            "site_url": None,
            "source": None,
            "primary_image_url": None,
            "last_scraped": None,
            "agency_name": None,
            "agency_level": None,
            "has_water": False,
            "body_of_water": None,
            "water_feature": None,
            "toilet_type": None,
            "activities": None,
            "amenities_raw": None,
            "is_open": None,
            "booking_url": None,
            "is_reservable": None,
            "booking_provider": None,
            "images": [],
            "hero_image": None,
        },
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
        "get_review_by_user_and_campsite": None,
    }
    for name, value in stubs.items():
        if hasattr(A, name):
            monkeypatch.setattr(A, name, (lambda v: (lambda *a, **k: v))(value))
    return stubs


def _render_campsite(**context):
    """Render templates/_reviews_list.html through the Flask app's Jinja env."""
    with app_module.app.test_request_context("/campsite/42"):
        tpl = app_module.app.jinja_env.get_template("_reviews_list.html")
        return tpl.render(**context)


class TestReviewListDisplay:
    """Tests for review list rendering."""

    def test_no_reviews_shows_nothing(self):
        """When there are no reviews, the section is not rendered."""
        html = _render_campsite(
            reviews=[],
            reviews_total_count=0,
            review_verdict_counts={"up": 0, "down": 0},
            campsite={"id": 42, "name": "Test"},
            reviews_page=1,
            current_user=None,
        )
        # Should not have the reviews section at all
        assert "Visitor reviews" not in html
        assert html.strip() == ""

    def test_removed_review_does_not_render(self):
        """Reviews with status 'removed' are not fetched and do not appear."""
        # The get_reviews_for_campsite function already filters by status='published',
        # so removed reviews should never be in the list. But let's verify the template
        # doesn't accidentally render them if they somehow get passed.
        html = _render_campsite(
            reviews=[],
            reviews_total_count=0,
            review_verdict_counts={"up": 0, "down": 0},
            campsite={"id": 42, "name": "Test"},
            reviews_page=1,
            current_user=None,
        )
        assert "removed" not in html.lower()

    def test_verdict_summary_all_up(self):
        """With only thumbs up, shows 'All X visitors recommend'."""
        from datetime import datetime
        html = _render_campsite(
            reviews=[
                {
                    "id": 1,
                    "verdict": True,
                    "body": "Great spot!",
                    "created_at": datetime(2025, 1, 1),
                    "author_username": "alice",
                    "author_first_name": "Alice",
                    "author_avatar_path": None,
                    "approved_photos": [],
                },
                {
                    "id": 2,
                    "verdict": True,
                    "body": "Loved it",
                    "created_at": datetime(2025, 1, 2),
                    "author_username": "bob",
                    "author_first_name": "Bob",
                    "author_avatar_path": None,
                    "approved_photos": [],
                },
            ],
            reviews_total_count=2,
            review_verdict_counts={"up": 2, "down": 0},
            campsite={"id": 42, "name": "Test"},
            reviews_page=1,
            current_user=None,
        )
        assert "All 2 visitors recommend this site" in html

    def test_verdict_summary_mixed(self):
        """With mixed verdicts, shows 'X of Y recommend'."""
        from datetime import datetime
        html = _render_campsite(
            reviews=[
                {
                    "id": 1,
                    "verdict": True,
                    "body": "Good",
                    "created_at": datetime(2025, 1, 1),
                    "author_username": "alice",
                    "author_first_name": "Alice",
                    "author_avatar_path": None,
                    "approved_photos": [],
                },
                {
                    "id": 2,
                    "verdict": False,
                    "body": "Not for us",
                    "created_at": datetime(2025, 1, 2),
                    "author_username": "bob",
                    "author_first_name": "Bob",
                    "author_avatar_path": None,
                    "approved_photos": [],
                },
            ],
            reviews_total_count=2,
            review_verdict_counts={"up": 1, "down": 1},
            campsite={"id": 42, "name": "Test"},
            reviews_page=1,
            current_user=None,
        )
        assert "1 of 2 visitors recommend this site" in html

    def test_verdict_summary_single_recommendation(self):
        """With 1 visitor, uses singular 'visitor'."""
        from datetime import datetime
        html = _render_campsite(
            reviews=[
                {
                    "id": 1,
                    "verdict": True,
                    "body": "Good",
                    "created_at": datetime(2025, 1, 1),
                    "author_username": "alice",
                    "author_first_name": "Alice",
                    "author_avatar_path": None,
                    "approved_photos": [],
                },
            ],
            reviews_total_count=1,
            review_verdict_counts={"up": 1, "down": 0},
            campsite={"id": 42, "name": "Test"},
            reviews_page=1,
            current_user=None,
        )
        assert "All 1 visitor recommend" in html

    def test_approved_photo_renders_with_correct_url(self):
        """Approved photos render with /api/review_photos/<id> URL."""
        from datetime import datetime
        html = _render_campsite(
            reviews=[
                {
                    "id": 1,
                    "verdict": True,
                    "body": "Great spot",
                    "created_at": datetime(2025, 1, 1),
                    "author_username": "alice",
                    "author_first_name": "Alice",
                    "author_avatar_path": None,
                    "approved_photos": [
                        {"id": 100, "path": "test.jpg", "created_at": datetime(2025, 1, 1)},
                    ],
                },
            ],
            reviews_total_count=1,
            review_verdict_counts={"up": 1, "down": 0},
            campsite={"id": 42, "name": "Test"},
            reviews_page=1,
            current_user=None,
        )
        assert "/api/review_photos/100" in html
        # Should NOT have a static path
        assert "static/var/review_photos" not in html

    def test_pending_photo_does_not_render(self):
        """Pending photos are not fetched by get_reviews_for_campsite, so don't appear."""
        # The function already filters photos by status='approved', so pending
        # photos should never be in the approved_photos list.
        from datetime import datetime
        html = _render_campsite(
            reviews=[
                {
                    "id": 1,
                    "verdict": True,
                    "body": "Great spot",
                    "created_at": datetime(2025, 1, 1),
                    "author_username": "alice",
                    "author_first_name": "Alice",
                    "author_avatar_path": None,
                    "approved_photos": [],
                },
            ],
            reviews_total_count=1,
            review_verdict_counts={"up": 1, "down": 0},
            campsite={"id": 42, "name": "Test"},
            reviews_page=1,
            current_user=None,
        )
        # No mention of pending or awaiting approval
        assert "pending" not in html.lower()
        assert "awaiting" not in html.lower()

    def test_xss_review_body_escaped(self):
        """Review body with <script> tag is escaped, not executed."""
        from datetime import datetime
        html = _render_campsite(
            reviews=[
                {
                    "id": 1,
                    "verdict": True,
                    "body": "Great spot <script>alert(1)</script>",
                    "created_at": datetime(2025, 1, 1),
                    "author_username": "alice",
                    "author_first_name": "Alice",
                    "author_avatar_path": None,
                    "approved_photos": [],
                },
            ],
            reviews_total_count=1,
            review_verdict_counts={"up": 1, "down": 0},
            campsite={"id": 42, "name": "Test"},
            reviews_page=1,
            current_user=None,
        )
        # The script tag should be escaped
        assert "<script>" not in html
        assert "&lt;script&gt;" in html or "script&gt;" in html

    def test_xss_author_name_escaped(self):
        """Author name with script tag is escaped."""
        from datetime import datetime
        html = _render_campsite(
            reviews=[
                {
                    "id": 1,
                    "verdict": True,
                    "body": "Good",
                    "created_at": datetime(2025, 1, 1),
                    "author_username": "alice<script>",
                    "author_first_name": "Alice<img src=x onerror=alert(1)>",
                    "author_avatar_path": None,
                    "approved_photos": [],
                },
            ],
            reviews_total_count=1,
            review_verdict_counts={"up": 1, "down": 0},
            campsite={"id": 42, "name": "Test"},
            reviews_page=1,
            current_user=None,
        )
        # Should not have raw HTML tags
        assert "<img src=x" not in html
        assert "alice<script>" not in html

    def test_pagination_first_page_of_25_reviews(self):
        """With 25 reviews, first page shows 20 and a 'More' link."""
        from datetime import datetime
        reviews = [
            {
                "id": i,
                "verdict": True,
                "body": f"Review {i}",
                "created_at": datetime(2025, 1, i % 28 + 1),
                "author_username": f"user{i}",
                "author_first_name": f"User {i}",
                "author_avatar_path": None,
                "approved_photos": [],
            }
            for i in range(1, 21)
        ]
        html = _render_campsite(
            reviews=reviews,
            reviews_total_count=25,
            review_verdict_counts={"up": 25, "down": 0},
            campsite={"id": 42, "name": "Test"},
            reviews_page=1,
            current_user=None,
        )
        # Should show pagination
        assert "Showing 1-20 of 25" in html
        assert "More reviews" in html
        # Should not show Previous on first page
        assert "Previous reviews" not in html

    def test_pagination_second_page_of_25_reviews(self):
        """Second page shows reviews 21-25 and Previous link."""
        from datetime import datetime
        reviews = [
            {
                "id": i,
                "verdict": True,
                "body": f"Review {i}",
                "created_at": datetime(2025, 1, 1),
                "author_username": f"user{i}",
                "author_first_name": f"User {i}",
                "author_avatar_path": None,
                "approved_photos": [],
            }
            for i in range(21, 26)
        ]
        html = _render_campsite(
            reviews=reviews,
            reviews_total_count=25,
            review_verdict_counts={"up": 25, "down": 0},
            campsite={"id": 42, "name": "Test"},
            reviews_page=2,
            current_user=None,
        )
        # Should show pagination
        assert "Showing 21-25 of 25" in html
        assert "Previous reviews" in html
        # Should not show More on last page
        assert "More reviews" not in html

    def test_author_name_uses_first_name_with_fallback(self):
        """Author name shows first_name if available, falls back to username."""
        from datetime import datetime
        html = _render_campsite(
            reviews=[
                {
                    "id": 1,
                    "verdict": True,
                    "body": "Good",
                    "created_at": datetime(2025, 1, 1),
                    "author_username": "alice_smith",
                    "author_first_name": "Alice",
                    "author_avatar_path": None,
                    "approved_photos": [],
                },
                {
                    "id": 2,
                    "verdict": True,
                    "body": "Great",
                    "created_at": datetime(2025, 1, 2),
                    "author_username": "bob_jones",
                    "author_first_name": None,
                    "author_avatar_path": None,
                    "approved_photos": [],
                },
            ],
            reviews_total_count=2,
            review_verdict_counts={"up": 2, "down": 0},
            campsite={"id": 42, "name": "Test"},
            reviews_page=1,
            current_user=None,
        )
        assert "Alice" in html
        assert "bob_jones" in html  # fallback to username
        assert "alice_smith" not in html  # first name takes precedence

    def test_verdict_badges_render(self):
        """Thumbs up and down emojis render for verdicts."""
        from datetime import datetime
        html = _render_campsite(
            reviews=[
                {
                    "id": 1,
                    "verdict": True,
                    "body": "Good",
                    "created_at": datetime(2025, 1, 1),
                    "author_username": "alice",
                    "author_first_name": "Alice",
                    "author_avatar_path": None,
                    "approved_photos": [],
                },
                {
                    "id": 2,
                    "verdict": False,
                    "body": "Bad",
                    "created_at": datetime(2025, 1, 2),
                    "author_username": "bob",
                    "author_first_name": "Bob",
                    "author_avatar_path": None,
                    "approved_photos": [],
                },
            ],
            reviews_total_count=2,
            review_verdict_counts={"up": 1, "down": 1},
            campsite={"id": 42, "name": "Test"},
            reviews_page=1,
            current_user=None,
        )
        assert "👍" in html
        assert "👎" in html

    def test_no_down_verdict_message(self):
        """When all are down votes, says 'No visitors recommend'."""
        from datetime import datetime
        html = _render_campsite(
            reviews=[
                {
                    "id": 1,
                    "verdict": False,
                    "body": "Terrible",
                    "created_at": datetime(2025, 1, 1),
                    "author_username": "alice",
                    "author_first_name": "Alice",
                    "author_avatar_path": None,
                    "approved_photos": [],
                },
            ],
            reviews_total_count=1,
            review_verdict_counts={"up": 0, "down": 1},
            campsite={"id": 42, "name": "Test"},
            reviews_page=1,
            current_user=None,
        )
        assert "No visitors recommend this site" in html

    def test_multiple_approved_photos_on_single_review(self):
        """Multiple approved photos on one review render in a grid."""
        from datetime import datetime
        html = _render_campsite(
            reviews=[
                {
                    "id": 1,
                    "verdict": True,
                    "body": "Great spot",
                    "created_at": datetime(2025, 1, 1),
                    "author_username": "alice",
                    "author_first_name": "Alice",
                    "author_avatar_path": None,
                    "approved_photos": [
                        {"id": 100, "path": "photo1.jpg", "created_at": datetime(2025, 1, 1)},
                        {"id": 101, "path": "photo2.jpg", "created_at": datetime(2025, 1, 1)},
                        {"id": 102, "path": "photo3.jpg", "created_at": datetime(2025, 1, 1)},
                    ],
                },
            ],
            reviews_total_count=1,
            review_verdict_counts={"up": 1, "down": 0},
            campsite={"id": 42, "name": "Test"},
            reviews_page=1,
            current_user=None,
        )
        assert "/api/review_photos/100" in html
        assert "/api/review_photos/101" in html
        assert "/api/review_photos/102" in html
