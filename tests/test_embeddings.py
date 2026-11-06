"""Tests for campsite embeddings (migration 0026).

Tests cover:
- Embedding provider batching and dimension validation
- Document composition with and without overview (39% case)
- Hash stability for change detection
- Job script dry-run and commit modes
- Migration idempotency

All tests run with no database and no network.
"""

import pytest
import os
import tempfile
import hashlib
from unittest.mock import patch, MagicMock, call
import json


class TestEmbeddingProvider:
    """Tests for the EmbeddingProvider interface."""

    def test_provider_defaults(self):
        """Provider uses sensible defaults when env vars are not set."""
        env_vars = {k: v for k, v in os.environ.items() if not k.startswith("EMBEDDINGS_")}
        with patch.dict(os.environ, env_vars, clear=True):
            from embeddings import EmbeddingProvider
            provider = EmbeddingProvider()
            assert provider.model == "nomic-embed-text"
            assert provider.timeout == 300  # raised: a CPU batch needs minutes, not 60s
            assert provider.max_retries == 3
            assert provider.expected_dimension == 768

    def test_provider_reads_env_vars(self):
        """Provider reads configuration from environment."""
        from embeddings import EmbeddingProvider
        env = {
            "EMBEDDINGS_BASE_URL": "http://example.com/v1",
            "EMBEDDINGS_MODEL": "custom-model",
            "EMBEDDINGS_TIMEOUT": "120",
            "EMBEDDINGS_MAX_RETRIES": "5",
        }
        with patch.dict(os.environ, env, clear=False):
            provider = EmbeddingProvider()
            assert provider.base_url == "http://example.com/v1"
            assert provider.model == "custom-model"
            assert provider.timeout == 120
            assert provider.max_retries == 5

    def test_provider_rejects_wrong_dimension(self):
        """Provider raises clear error when service returns wrong dimension."""
        from embeddings import EmbeddingProvider, EmbeddingDimensionError

        provider = EmbeddingProvider()
        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "data": [
                    {"embedding": [0.1] * 1536}  # Wrong dimension!
                ]
            }
            mock_post.return_value = mock_response

            with pytest.raises(EmbeddingDimensionError) as exc_info:
                provider.embed(["test text"])

            assert "Expected 768-dimensional" in str(exc_info.value)
            assert "got 1536-dimensional" in str(exc_info.value)

    def test_provider_batches_requests(self):
        """Provider makes one request for N texts, not N requests."""
        from embeddings import EmbeddingProvider

        provider = EmbeddingProvider()
        texts = ["text1", "text2", "text3"]

        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "data": [
                    {"embedding": [0.1] * 768},
                    {"embedding": [0.2] * 768},
                    {"embedding": [0.3] * 768},
                ]
            }
            mock_post.return_value = mock_response

            result = provider.embed(texts)

            # Should make exactly one POST request
            assert mock_post.call_count == 1
            call_args = mock_post.call_args
            assert len(call_args[1]["json"]["input"]) == 3
            assert result == [[0.1] * 768, [0.2] * 768, [0.3] * 768]

    def test_provider_retries_on_timeout(self):
        """Provider retries on timeout, giving up after max_retries."""
        from embeddings import EmbeddingProvider, EmbeddingError
        import requests

        provider = EmbeddingProvider()
        provider.max_retries = 2

        with patch("requests.post") as mock_post:
            mock_post.side_effect = requests.exceptions.Timeout("timeout")

            with pytest.raises(EmbeddingError) as exc_info:
                provider.embed(["test"])

            assert mock_post.call_count == 2

    def test_provider_returns_empty_for_empty_input(self):
        """Provider returns empty list for empty input."""
        from embeddings import EmbeddingProvider

        provider = EmbeddingProvider()
        result = provider.embed([])
        assert result == []


class TestDocumentComposition:
    """Tests for compositing campsite documents."""

    def test_document_with_no_overview_is_substantive(self):
        """A campsite with no overview still produces a meaningful document.

        This is the critical 39% case: campsites without an overview field
        must still embed meaningfully based on structured attributes.
        """
        from scripts.embed_campsites import compose_document

        # Minimal campsite with no overview
        row = (
            42,                           # id
            "Peaceful Meadow",            # name
            "Sierra National Forest",     # forest_name
            "foothills",                  # terrain
            ["hiking", "fishing"],        # activities
            "creek",                      # water_feature
            "vault",                      # toilet_type
            False,                        # is_free
            "recreation.gov",             # reservation_type
            2500,                         # elevation_ft
            None,                         # overview (NULL!)
        )

        doc = compose_document(row)

        # Document must include structured attributes, not be empty
        assert "Peaceful Meadow" in doc
        assert "Sierra National Forest" in doc
        assert "foothills" in doc
        assert "hiking" in doc or "fishing" in doc
        assert "creek" in doc
        assert "vault" in doc
        assert "Paid camping" in doc
        assert "recreation.gov" in doc
        assert "2500" in doc

    def test_document_with_overview_includes_it(self):
        """Overview is included in the document, in addition to attributes."""
        from scripts.embed_campsites import compose_document

        row = (
            42, "Meadow Camp", "Sierra", "foothills",
            ["hiking"], "creek", "vault", False, "recreation.gov", 2500,
            "A lovely meadow with great views"
        )

        doc = compose_document(row)

        # Should include both attributes and overview
        assert "Meadow Camp" in doc
        assert "A lovely meadow with great views" in doc

    def test_document_handles_none_attributes(self):
        """Document handles null values gracefully."""
        from scripts.embed_campsites import compose_document

        row = (
            42, None, None, None, None, None, None, None, None, None, None
        )

        doc = compose_document(row)

        # Should not crash and should produce a string
        assert isinstance(doc, str)
        assert len(doc) > 0

    def test_document_elevation_banding(self):
        """Document includes elevation band based on feet."""
        from scripts.embed_campsites import compose_document

        # Lowland
        row = (1, "Low", "F", "v", [], None, None, None, None, 500, None)
        doc = compose_document(row)
        assert "lowland" in doc

        # Foothills
        row = (2, "Mid", "F", "v", [], None, None, None, None, 2500, None)
        doc = compose_document(row)
        assert "foothills" in doc

        # Mountain
        row = (3, "High", "F", "v", [], None, None, None, None, 5000, None)
        doc = compose_document(row)
        assert "mountain" in doc

        # Alpine
        row = (4, "Very High", "F", "v", [], None, None, None, None, 9000, None)
        doc = compose_document(row)
        assert "alpine" in doc


class TestHashStability:
    """Tests for document hashing and change detection."""

    def test_hash_is_deterministic(self):
        """Same input produces same hash."""
        from scripts.embed_campsites import hash_text

        text = "This is a campsite document"
        hash1 = hash_text(text)
        hash2 = hash_text(text)
        assert hash1 == hash2

    def test_hash_differs_on_change(self):
        """Different input produces different hash."""
        from scripts.embed_campsites import hash_text

        hash1 = hash_text("Document A")
        hash2 = hash_text("Document B")
        assert hash1 != hash2

    def test_hash_is_sha256(self):
        """Hash matches SHA-256."""
        from scripts.embed_campsites import hash_text

        text = "test text"
        computed = hash_text(text)
        expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert computed == expected


class TestJobScriptDryRun:
    """Tests for --dry-run mode (default)."""

    def test_dry_run_writes_nothing(self):
        """Dry-run mode writes no database rows."""
        from scripts.embed_campsites import main

        with patch("scripts.embed_campsites.get_conn") as mock_get_conn:
            mock_conn = MagicMock()
            mock_get_conn.return_value.__enter__.return_value = mock_conn

            # Set up cursor mocks to return our test data
            select_cursor = MagicMock()
            select_cursor.fetchall.side_effect = [
                [(1, "A", "F", "t", [], "w", "v", False, "r", 100, None)],  # campsites
                [],  # existing embeddings
            ]
            mock_conn.cursor.return_value = select_cursor

            with patch("builtins.print"):
                main(["--dry-run", "--limit", "1"])

            # Count how many times we tried to INSERT (should be 0 in dry-run)
            insert_calls = [c for c in select_cursor.execute.call_args_list
                           if c[0] and "INSERT" in str(c[0][0]).upper()]
            assert len(insert_calls) == 0, "dry-run should not INSERT anything"

    def test_dry_run_default_behavior(self):
        """--dry-run is the default."""
        from scripts.embed_campsites import parse_args

        args = parse_args([])
        assert args.dry_run is True

        args = parse_args(["--commit"])
        assert args.dry_run is False

    def test_dry_run_shows_sample_documents(self):
        """Dry-run prints sample documents."""
        from scripts.embed_campsites import main
        import io
        import sys

        with patch("scripts.embed_campsites.get_conn") as mock_get_conn:
            mock_conn = MagicMock()
            mock_get_conn.return_value.__enter__.return_value = mock_conn
            mock_conn.cursor.return_value.fetchall.side_effect = [
                [
                    (1, "Camp A", "Forest", "terrain", [], "water", "toilet", False, "res", 1000, None),
                    (2, "Camp B", "Forest", "terrain", [], "water", "toilet", False, "res", 1000, None),
                ],
                [],
            ]

            output = []
            with patch("builtins.print", side_effect=lambda *a, **k: output.append(str(a))):
                main(["--dry-run"])

            # Should mention dry run mode and samples
            output_str = " ".join(output)
            assert "DRY RUN" in output_str or "dry" in output_str.lower()


class TestJobScriptCommitMode:
    """Tests for --commit mode."""

    def test_commit_mode_writes_embeddings(self):
        """Commit mode writes embeddings to the database."""
        from scripts.embed_campsites import main, EmbeddingProvider

        with patch("scripts.embed_campsites.get_conn") as mock_get_conn, \
                patch("scripts.embed_campsites.scrape_run") as mock_scrape, \
                patch.object(EmbeddingProvider, "embed", return_value=[[0.1] * 768]):
            mock_conn = MagicMock()
            mock_get_conn.return_value.__enter__.return_value = mock_conn
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchall.side_effect = [
                [(1, "Camp", "F", "t", [], "w", "v", False, "r", 100, None)],  # campsites
                [],  # existing embeddings
            ]

            mock_scrape_ctx = MagicMock()
            mock_scrape_ctx.seen = 0
            mock_scrape_ctx.upserted = 0
            mock_scrape.return_value.__enter__.return_value = mock_scrape_ctx

            with patch("builtins.print"):
                main(["--commit", "--limit", "1"])

            # Should have called embed and written to database
            insert_calls = [c for c in mock_cursor.execute.call_args_list
                           if c[0] and "INSERT" in str(c[0][0]).upper()]
            assert len(insert_calls) > 0, "commit mode should INSERT embeddings"

    def test_commit_mode_skips_unchanged_rows(self):
        """Commit mode skips rows whose hash hasn't changed."""
        from scripts.embed_campsites import main, EmbeddingProvider

        campsite_row = (1, "Camp", "F", "t", [], "w", "v", False, "r", 100, None)

        with patch("scripts.embed_campsites.get_conn") as mock_get_conn, \
                patch("scripts.embed_campsites.scrape_run") as mock_scrape, \
                patch.object(EmbeddingProvider, "embed", return_value=[]) as mock_embed:
            mock_conn = MagicMock()
            mock_get_conn.return_value.__enter__.return_value = mock_conn
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value = mock_cursor

            # Simulate that existing embedding hash matches (unchanged)
            from scripts.embed_campsites import hash_text, compose_document
            doc = compose_document(campsite_row)
            test_hash = hash_text(doc)

            # Campsite exists, hash matches (unchanged)
            mock_cursor.fetchall.side_effect = [
                [campsite_row],
                [(1, test_hash)],  # existing embedding with same hash
            ]

            mock_scrape_ctx = MagicMock()
            mock_scrape_ctx.seen = 0
            mock_scrape_ctx.upserted = 0
            mock_scrape.return_value.__enter__.return_value = mock_scrape_ctx

            with patch("builtins.print"):
                main(["--commit", "--limit", "1"])

            # Should not have called embed for unchanged row
            mock_embed.assert_not_called()


class TestMigrationIdempotency:
    """Tests for migration 0026 idempotency."""

    def test_migration_has_if_not_exists_guards(self):
        """Migration uses IF NOT EXISTS on all CREATE statements."""
        with open("/home/arrowboy/Projects/campSearch/db/migrations/0026_campsite_embeddings.sql") as f:
            migration = f.read()

        # Should have IF NOT EXISTS on extension and table
        assert "CREATE EXTENSION IF NOT EXISTS vector" in migration
        assert "CREATE TABLE IF NOT EXISTS campsite_embeddings" in migration
        assert "CREATE INDEX IF NOT EXISTS" in migration

    def test_migration_has_no_alter_table(self):
        """Migration never uses ALTER TABLE ADD CONSTRAINT (not idempotent)."""
        with open("/home/arrowboy/Projects/campSearch/db/migrations/0026_campsite_embeddings.sql") as f:
            migration = f.read()

        assert "ALTER TABLE" not in migration or "ADD CONSTRAINT" not in migration

    def test_migration_has_cascade_delete(self):
        """Migration uses CASCADE on campsite_id foreign key."""
        with open("/home/arrowboy/Projects/campSearch/db/migrations/0026_campsite_embeddings.sql") as f:
            migration = f.read()

        assert "ON DELETE CASCADE" in migration

    def test_migration_defines_hnsw_index(self):
        """Migration creates HNSW index for cosine similarity."""
        with open("/home/arrowboy/Projects/campSearch/db/migrations/0026_campsite_embeddings.sql") as f:
            migration = f.read()

        assert "HNSW" in migration.upper() or "hnsw" in migration
        assert "vector_cosine_ops" in migration or "cosine" in migration


class TestIntegration:
    """Integration tests for the full pipeline."""

    def test_compose_then_hash_is_stable(self):
        """Compositing then hashing is stable across runs."""
        from scripts.embed_campsites import compose_document, hash_text

        row = (42, "A", "B", "C", ["d"], "e", "f", False, "g", 1000, "h")
        doc1 = compose_document(row)
        hash1 = hash_text(doc1)

        doc2 = compose_document(row)
        hash2 = hash_text(doc2)

        assert hash1 == hash2

    def test_different_campsites_have_different_hashes(self):
        """Different campsites produce different hashes."""
        from scripts.embed_campsites import compose_document, hash_text

        row1 = (1, "Camp A", "Forest A", "terrain1", ["hiking"], None, None, True, None, 1000, None)
        row2 = (2, "Camp B", "Forest B", "terrain2", ["fishing"], None, None, False, None, 2000, None)

        hash1 = hash_text(compose_document(row1))
        hash2 = hash_text(compose_document(row2))

        assert hash1 != hash2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
