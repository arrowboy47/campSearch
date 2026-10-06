"""Tests for migration 0023_user_events.sql.

Pure text assertions on the SQL file, no database connection.
Verifies the schema definition matches specifications.
"""

import os


def test_migration_file_exists():
    """The migration file exists and is non-empty."""
    migration_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "db",
        "migrations",
        "0023_user_events.sql"
    )
    assert os.path.exists(migration_path), "Migration file does not exist"
    assert os.path.getsize(migration_path) > 0, "Migration file is empty"


def test_all_columns_present():
    """All 12 required columns appear in the CREATE TABLE statement."""
    migration_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "db",
        "migrations",
        "0023_user_events.sql"
    )
    with open(migration_path) as f:
        content = f.read()

    required_columns = [
        "id",
        "user_id",
        "anon_id",
        "session_id",
        "event_type",
        "campsite_id",
        "position",
        "query_text",
        "filters",
        "result_ids",
        "meta",
        "created_at"
    ]

    for col in required_columns:
        assert col in content, f"Column '{col}' not found in migration"


def test_all_indexes_present():
    """All four index names appear in the migration."""
    migration_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "db",
        "migrations",
        "0023_user_events.sql"
    )
    with open(migration_path) as f:
        content = f.read()

    required_indexes = [
        "user_events_user_created_idx",
        "user_events_anon_created_idx",
        "user_events_campsite_type_idx",
        "user_events_type_created_idx"
    ]

    for idx in required_indexes:
        assert idx in content, f"Index '{idx}' not found in migration"


def test_check_constraint_covers_all_event_types():
    """The CHECK constraint names all nine allowed event types."""
    migration_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "db",
        "migrations",
        "0023_user_events.sql"
    )
    with open(migration_path) as f:
        content = f.read()

    required_event_types = [
        "search_performed",
        "result_impression",
        "result_clicked",
        "campsite_viewed",
        "saved",
        "unsaved",
        "collection_added",
        "review_submitted",
        "search_feedback"
    ]

    for event_type in required_event_types:
        assert event_type in content, f"Event type '{event_type}' not in CHECK constraint"

    # Verify the constraint has the right name
    assert "user_events_event_type_check" in content


def test_foreign_keys_use_set_null():
    """Both foreign keys use ON DELETE SET NULL, not CASCADE."""
    migration_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "db",
        "migrations",
        "0023_user_events.sql"
    )
    with open(migration_path) as f:
        content = f.read()

    # Check both SET NULL references are present
    assert "REFERENCES users (id) ON DELETE SET NULL" in content, \
        "user_id foreign key does not use ON DELETE SET NULL"
    assert "REFERENCES campsites (id) ON DELETE SET NULL" in content, \
        "campsite_id foreign key does not use ON DELETE SET NULL"

    # Ensure CASCADE is not used
    assert "CASCADE" not in content, "Migration uses CASCADE, should use SET NULL"


def test_if_not_exists_guards():
    """The migration uses IF NOT EXISTS to be safe to re-run."""
    migration_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "db",
        "migrations",
        "0023_user_events.sql"
    )
    with open(migration_path) as f:
        content = f.read()

    assert "IF NOT EXISTS" in content, "Migration does not use IF NOT EXISTS guard"


def test_no_data_mutation():
    """The migration only creates; it does not contain UPDATE or DELETE statements."""
    migration_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "db",
        "migrations",
        "0023_user_events.sql"
    )
    with open(migration_path) as f:
        content = f.read()

    # Check that UPDATE is not present (allow UPDATE in comments)
    lines = content.split("\n")
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        assert not stripped.startswith("UPDATE"), \
            "Migration should not contain UPDATE statements"
        assert not stripped.startswith("DELETE"), \
            "Migration should not contain DELETE statements"


def _sql():
    import os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "db", "migrations", "0023_user_events.sql")
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def _statements(sql):
    """Executable lines only, comments stripped."""
    return "\n".join(l for l in sql.splitlines()
                     if not l.strip().startswith("--"))


def test_no_unguarded_alter_statements():
    """Every statement must survive a second run.

    Postgres has no `ADD CONSTRAINT IF NOT EXISTS`, so a constraint added via
    ALTER makes the migration fail the second time it is applied. Verified by
    actually applying this file twice inside a rolled-back transaction: with
    the ALTER form the second apply raised `constraint ... already exists`.
    Grepping for the string "IF NOT EXISTS" does not catch that, because the
    table and indexes supply the string while the ALTER remains unguarded.
    The constraint therefore has to be declared inline in CREATE TABLE.
    """
    body = _statements(_sql())
    assert "ALTER TABLE" not in body.upper(), (
        "an ALTER statement cannot be made idempotent here; declare the "
        "constraint inline in CREATE TABLE instead")


def test_check_constraint_is_inline_in_create_table():
    body = _statements(_sql())
    create = body[body.upper().index("CREATE TABLE"):]
    create = create[:create.index(");") + 2]
    assert "user_events_event_type_check" in create, (
        "the CHECK constraint must live inside CREATE TABLE so the table's "
        "own IF NOT EXISTS guard covers it")


def test_every_executable_statement_is_guarded():
    body = _statements(_sql())
    import re
    for stmt in [s.strip() for s in body.split(";") if s.strip()]:
        head = " ".join(stmt.split()[:3]).upper()
        assert "IF NOT EXISTS" in stmt.upper(), (
            "statement is not re-runnable: %s..." % head)
