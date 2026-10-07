"""Tests for migration 0025_admin_and_moderation.sql.

Pure text assertions on the SQL file, no database connection.
Verifies the schema definition matches specifications.
"""

import os
import re


def _migration_path():
    """Return the path to the migration file."""
    return os.path.join(
        os.path.dirname(__file__),
        "..",
        "db",
        "migrations",
        "0025_admin_and_moderation.sql"
    )


def _sql():
    """Load the migration SQL."""
    with open(_migration_path(), encoding="utf-8") as f:
        return f.read()


def _executable_statements(sql):
    """Return SQL with comment lines removed."""
    return "\n".join(
        line for line in sql.splitlines()
        if not line.strip().startswith("--")
    )


def test_migration_file_exists():
    """The migration file exists and is non-empty."""
    path = _migration_path()
    assert os.path.exists(path), "Migration file does not exist"
    assert os.path.getsize(path) > 0, "Migration file is empty"


def test_is_admin_column_added():
    """The is_admin column is added to users with correct defaults."""
    sql = _sql()

    assert "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin" in sql, \
        "is_admin column not added with ADD COLUMN IF NOT EXISTS"
    assert "BOOLEAN NOT NULL DEFAULT FALSE" in sql, \
        "is_admin should be BOOLEAN NOT NULL DEFAULT FALSE"


def test_users_admin_index_created():
    """A partial index on users(id) WHERE is_admin is created."""
    sql = _sql()

    assert "users_admin_idx" in sql, "users_admin_idx index not found"
    assert "WHERE is_admin" in sql, "Partial index missing WHERE is_admin clause"


def test_moderation_actions_table_created():
    """The moderation_actions table is created with all required columns."""
    sql = _sql()

    assert "CREATE TABLE IF NOT EXISTS moderation_actions" in sql, \
        "moderation_actions table not created"

    required_columns = [
        "id",
        "admin_id",
        "action",
        "target_type",
        "target_id",
        "reason",
        "created_at"
    ]

    for col in required_columns:
        assert col in sql, f"Column '{col}' not found in moderation_actions"


def test_content_reports_table_created():
    """The content_reports table is created with all required columns."""
    sql = _sql()

    assert "CREATE TABLE IF NOT EXISTS content_reports" in sql, \
        "content_reports table not created"

    required_columns = [
        "id",
        "reporter_id",
        "target_type",
        "target_id",
        "reason",
        "status",
        "created_at"
    ]

    for col in required_columns:
        assert col in sql, f"Column '{col}' not found in content_reports"


def test_all_indexes_created():
    """All four index names appear in the migration."""
    sql = _sql()

    required_indexes = [
        "users_admin_idx",
        "moderation_actions_target_idx",
        "moderation_actions_admin_created_idx",
        "content_reports_status_idx"
    ]

    for idx_name in required_indexes:
        assert idx_name in sql, f"Missing index: {idx_name}"


def test_no_bare_alter_constraints():
    """No bare ALTER TABLE ADD CONSTRAINT statements (must be inline)."""
    sql = _executable_statements(_sql())

    # Check for ALTER TABLE ... ADD CONSTRAINT pattern (not IF NOT EXISTS guarded)
    alter_constraint_pattern = r"ALTER\s+TABLE\s+\w+\s+ADD\s+CONSTRAINT"
    matches = re.findall(alter_constraint_pattern, sql, re.IGNORECASE)

    assert not matches, \
        "ALTER TABLE ADD CONSTRAINT found; constraints must be inline in CREATE TABLE"


def test_every_executable_statement_is_guarded():
    """Every executable CREATE/ALTER statement uses IF NOT EXISTS."""
    sql = _executable_statements(_sql())

    statements = [s.strip() for s in sql.split(";") if s.strip()]

    for stmt in statements:
        words = stmt.split()
        if words and words[0].upper() in ("CREATE", "ALTER"):
            assert "IF NOT EXISTS" in stmt.upper(), \
                f"Statement not guarded: {stmt[:60]}..."


def test_moderation_actions_admin_id_set_null():
    """moderation_actions.admin_id uses ON DELETE SET NULL."""
    sql = _sql()

    # Find the moderation_actions table definition
    start = sql.find("CREATE TABLE IF NOT EXISTS moderation_actions")
    end = sql.find(");", start) + 2
    table_def = sql[start:end]

    assert "admin_id" in table_def, "admin_id column not found"
    assert "REFERENCES users (id) ON DELETE SET NULL" in table_def, \
        "admin_id should use ON DELETE SET NULL"


def test_no_cascade_in_moderation():
    """The word CASCADE does not appear in the migration (audit tables use SET NULL)."""
    sql = _sql()

    # CASCADE should not appear in the file (it's for authored content, not audit logs)
    assert "CASCADE" not in sql, \
        "Migration should not contain CASCADE; use ON DELETE SET NULL for audit tables"


def test_moderation_actions_checks():
    """moderation_actions has correct CHECK constraints for action and target_type."""
    sql = _sql()

    # Check for action constraint
    assert "moderation_actions_action_check" in sql, "Missing moderation_actions_action_check"
    assert "'approve'" in sql, "Missing 'approve' action"
    assert "'reject'" in sql, "Missing 'reject' action"
    assert "'remove'" in sql, "Missing 'remove' action"
    assert "'restore'" in sql, "Missing 'restore' action"
    assert "'dismiss'" in sql, "Missing 'dismiss' action"

    # Check for target_type constraint
    assert "moderation_actions_target_type_check" in sql, "Missing moderation_actions_target_type_check"
    assert "'review'" in sql, "Missing 'review' target_type"
    assert "'review_photo'" in sql, "Missing 'review_photo' target_type"
    assert "'attribute_report'" in sql, "Missing 'attribute_report' target_type"


def test_content_reports_checks():
    """content_reports has correct CHECK constraints for target_type and status."""
    sql = _sql()

    # Check for target_type constraint
    assert "content_reports_target_type_check" in sql, "Missing content_reports_target_type_check"

    # Check for status constraint
    assert "content_reports_status_check" in sql, "Missing content_reports_status_check"
    assert "'open'" in sql, "Missing 'open' status"
    assert "'actioned'" in sql, "Missing 'actioned' status"
    assert "'dismissed'" in sql, "Missing 'dismissed' status"


def test_content_reports_status_default():
    """content_reports.status defaults to 'open'."""
    sql = _sql()

    start = sql.find("CREATE TABLE IF NOT EXISTS content_reports")
    end = sql.find(");", start) + 2
    table_def = sql[start:end]

    assert "DEFAULT 'open'" in table_def, \
        "status column should default to 'open'"


def test_content_reports_reporter_id_set_null():
    """content_reports.reporter_id uses ON DELETE SET NULL."""
    sql = _sql()

    start = sql.find("CREATE TABLE IF NOT EXISTS content_reports")
    end = sql.find(");", start) + 2
    table_def = sql[start:end]

    assert "REFERENCES users (id) ON DELETE SET NULL" in table_def, \
        "reporter_id should use ON DELETE SET NULL"


def test_no_update_or_delete_statements():
    """No UPDATE or DELETE statements appear in the migration."""
    sql = _executable_statements(_sql())

    lines = [line.strip() for line in sql.split("\n") if line.strip()]

    for line in lines:
        if line.startswith("--"):
            continue
        assert not line.startswith("UPDATE"), \
            "Migration should not contain UPDATE statements"
        assert not line.startswith("DELETE"), \
            "Migration should not contain DELETE statements"


def test_indexes_match_conventions():
    """All indexes follow the naming convention <table>_<columns>_idx."""
    sql = _sql()

    # Extract all index names
    index_pattern = r"CREATE INDEX IF NOT EXISTS (\w+)"
    indexes = re.findall(index_pattern, sql)

    for idx_name in indexes:
        # Should contain underscore separating table from columns
        assert "_" in idx_name, f"Index name '{idx_name}' does not follow convention"
        assert idx_name.endswith("_idx"), f"Index name '{idx_name}' should end with _idx"


def test_bigserial_primary_keys():
    """Both new tables use BIGSERIAL for primary key."""
    sql = _sql()

    assert sql.count("BIGSERIAL PRIMARY KEY") >= 2, \
        "Both moderation_actions and content_reports should use BIGSERIAL PRIMARY KEY"
