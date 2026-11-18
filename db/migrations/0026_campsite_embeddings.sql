-- 0026_campsite_embeddings
-- Semantic vector embeddings of composited campsite documents (768-dimensional).
--
--   campsite_id       - foreign key to campsites; cascade on delete because embeddings
--                       are derived data and meaningless once a campsite is gone (unlike
--                       user_events, which audit trails that must outlive the campsite).
--   embedding         - pgvector(768) stored as 32-bit floats, hnsw indexed for cosine
--                       similarity search. The nomic-embed-text model produces 768-dim
--                       vectors; mismatch is caught at insert time with dimension check.
--   source_text_hash  - SHA-256 hash of the composited document used to generate this
--                       embedding. Allows re-embedding only when attributes change,
--                       so a second full run is nearly free if nothing changed.
--   model             - name of the embedding model (e.g., 'nomic-embed-text') for
--                       future migration when models change.
--   updated_at        - timestamp when the embedding was created or refreshed.
--
-- The CHECK constraints are declared inline to ensure idempotency. Postgres has no
-- ADD CONSTRAINT IF NOT EXISTS, so a separate ALTER would fail on a second run.
-- CREATE EXTENSION must succeed as the campsearch role; if that role lacks superuser,
-- a human must apply the extension first.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS campsite_embeddings (
    campsite_id       INTEGER PRIMARY KEY REFERENCES campsites (id) ON DELETE CASCADE,
    embedding         vector(768) NOT NULL,
    source_text_hash  TEXT NOT NULL,
    model             TEXT NOT NULL,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS campsite_embeddings_hnsw ON campsite_embeddings
    USING hnsw (embedding vector_cosine_ops);
