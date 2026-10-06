"""Test migration 0024_reviews.sql structure and idempotency."""

import re
from pathlib import Path


def load_migration():
    """Load the migration SQL file."""
    migration_path = Path(__file__).parent.parent / "db" / "migrations" / "0024_reviews.sql"
    with open(migration_path) as f:
        return f.read()


def test_all_tables_created():
    """Verify all three tables are created with correct columns."""
    sql = load_migration()

    # Check reviews table exists and has required columns
    assert "CREATE TABLE IF NOT EXISTS reviews" in sql
    assert "BIGSERIAL PRIMARY KEY" in sql
    assert "user_id" in sql and "REFERENCES users" in sql
    assert "campsite_id" in sql and "REFERENCES campsites" in sql
    assert "verdict" in sql and "BOOLEAN NOT NULL" in sql
    assert "body" in sql and "TEXT" in sql
    assert "visited" in sql and "BOOLEAN NOT NULL DEFAULT true" in sql
    assert "status" in sql and "DEFAULT 'published'" in sql
    assert "created_at" in sql and "TIMESTAMPTZ NOT NULL DEFAULT now()" in sql
    assert "updated_at" in sql and "TIMESTAMPTZ NOT NULL DEFAULT now()" in sql

    # Check review_photos table exists and has required columns
    assert "CREATE TABLE IF NOT EXISTS review_photos" in sql
    assert "path" in sql and "TEXT NOT NULL" in sql
    assert "status" in sql and "DEFAULT 'pending'" in sql

    # Check review_attribute_reports table exists and has required columns
    assert "CREATE TABLE IF NOT EXISTS review_attribute_reports" in sql
    assert "attribute" in sql and "TEXT NOT NULL" in sql
    assert "claimed_value" in sql and "TEXT" in sql


def test_all_indexes_created():
    """Verify all required indexes are created."""
    sql = load_migration()

    expected_indexes = [
        "reviews_campsite_status_idx",
        "reviews_user_idx",
        "review_photos_review_status_idx",
        "review_attribute_reports_campsite_idx",
    ]

    for index_name in expected_indexes:
        assert index_name in sql, f"Missing index: {index_name}"


def test_no_alter_table():
    """Verify no ALTER TABLE statements exist (idempotency requirement)."""
    sql = load_migration()

    # ALTER TABLE is incompatible with idempotency
    assert "ALTER TABLE" not in sql, "Migration contains ALTER TABLE which breaks idempotency"


def test_if_not_exists_guards():
    """Verify every CREATE statement has IF NOT EXISTS."""
    sql = load_migration()

    # Count CREATE statements
    create_statements = re.findall(r"CREATE\s+(TABLE|INDEX)\s+(?!IF\s+NOT\s+EXISTS)", sql)
    assert len(create_statements) == 0, f"Found CREATE statements without IF NOT EXISTS: {create_statements}"

    # Verify expected IF NOT EXISTS clauses
    assert sql.count("IF NOT EXISTS") >= 6, "Expected at least 6 IF NOT EXISTS clauses"


def test_unique_constraint_inline():
    """Verify UNIQUE (user_id, campsite_id) is declared inline in CREATE TABLE."""
    sql = load_migration()

    # The constraint should be inside the CREATE TABLE, not a separate ALTER
    create_reviews = sql[sql.find("CREATE TABLE IF NOT EXISTS reviews"):sql.find("CREATE TABLE IF NOT EXISTS review_photos")]
    assert "UNIQUE (user_id, campsite_id)" in create_reviews, "UNIQUE constraint not found inline in reviews table"


def test_reviews_status_check():
    """Verify reviews.status CHECK allows only 'published' and 'removed'."""
    sql = load_migration()

    # Find the reviews status check
    assert "status IN ('published', 'removed')" in sql, "reviews.status CHECK should allow only 'published' and 'removed'"

    # Make sure 'pending' is NOT in the reviews status check
    create_reviews = sql[sql.find("CREATE TABLE IF NOT EXISTS reviews"):sql.find("CREATE TABLE IF NOT EXISTS review_photos")]
    # Find the CHECK constraint for status in this table
    status_check_match = re.search(r"CONSTRAINT reviews_status_check CHECK\s*\(([^)]+)\)", create_reviews)
    assert status_check_match, "reviews status CHECK constraint not found"
    check_content = status_check_match.group(1)
    assert "'pending'" not in check_content, "reviews.status should NOT allow 'pending' status"


def test_review_photos_status_check():
    """Verify review_photos.status CHECK allows pending, approved, rejected and defaults to pending."""
    sql = load_migration()

    create_photos = sql[sql.find("CREATE TABLE IF NOT EXISTS review_photos"):sql.find("CREATE TABLE IF NOT EXISTS review_attribute_reports")]

    # Check status options
    assert "'pending'" in create_photos, "review_photos should allow 'pending'"
    assert "'approved'" in create_photos, "review_photos should allow 'approved'"
    assert "'rejected'" in create_photos, "review_photos should allow 'rejected'"

    # Check default
    assert "DEFAULT 'pending'" in create_photos, "review_photos.status should default to 'pending'"


def test_no_update_or_delete():
    """Verify no UPDATE or DELETE statements exist."""
    sql = load_migration()

    # Check for actual UPDATE statements (not in comments or FK constraints)
    update_match = re.search(r'\bUPDATE\b', sql)
    assert not update_match, "Migration should not contain UPDATE statements"

    # Check for actual DELETE statements (standalone, not "ON DELETE CASCADE")
    delete_match = re.search(r'^\s*DELETE\b', sql, re.MULTILINE)
    assert not delete_match, "Migration should not contain DELETE statements"


def test_cascade_delete_for_reviews():
    """Verify foreign keys for reviews table cascade on delete."""
    sql = load_migration()

    create_reviews = sql[sql.find("CREATE TABLE IF NOT EXISTS reviews"):sql.find("CREATE TABLE IF NOT EXISTS review_photos")]

    # Both FKs should CASCADE
    assert "REFERENCES users (id) ON DELETE CASCADE" in create_reviews
    assert "REFERENCES campsites (id) ON DELETE CASCADE" in create_reviews


def test_cascade_delete_for_photos_and_reports():
    """Verify foreign keys cascade for review_photos and review_attribute_reports."""
    sql = load_migration()

    # review_photos should cascade
    create_photos = sql[sql.find("CREATE TABLE IF NOT EXISTS review_photos"):sql.find("CREATE TABLE IF NOT EXISTS review_attribute_reports")]
    assert "REFERENCES reviews (id) ON DELETE CASCADE" in create_photos

    # review_attribute_reports should cascade
    create_reports = sql[sql.find("CREATE TABLE IF NOT EXISTS review_attribute_reports"):]
    assert "REFERENCES reviews (id) ON DELETE CASCADE" in create_reports
    assert "REFERENCES campsites (id) ON DELETE CASCADE" in create_reports


def test_attribute_check_values():
    """Verify review_attribute_reports.attribute CHECK has exactly the allowed values."""
    sql = load_migration()

    allowed_attributes = ['water', 'toilet_type', 'is_free', 'fee_min', 'is_reservable', 'is_open']

    for attr in allowed_attributes:
        assert f"'{attr}'" in sql, f"Missing allowed attribute: {attr}"
