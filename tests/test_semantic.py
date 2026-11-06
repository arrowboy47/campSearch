"""Tests for the semantic (embedding) retriever.

Every test here runs without a database and without an embedding service.
"""

import pytest

import semantic
from embeddings import EmbeddingError
from ranking_config import DEFAULT_CONFIG


class _FakeProvider:
    """Stands in for EmbeddingProvider without any network."""

    def __init__(self, vector=None, error=None):
        self.vector = vector if vector is not None else [0.1] * 768
        self.error = error
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.error:
            raise self.error
        return [self.vector for _ in texts]


# ========== embed_query ==========

def test_empty_query_is_not_embedded():
    """No query means no call to the embedding service at all."""
    provider = _FakeProvider()
    assert semantic.embed_query("", provider=provider) is None
    assert semantic.embed_query("   ", provider=provider) is None
    assert semantic.embed_query(None, provider=provider) is None
    assert provider.calls == []


def test_query_is_sent_without_a_task_prefix():
    """nomic-embed-text wants 'search_query: ', the documents were embedded
    without 'search_document: ', and a prefix on one side only would compare
    prefixed queries against unprefixed documents. Neither side gets one.
    """
    provider = _FakeProvider()
    semantic.embed_query("lake fishing", provider=provider)
    assert provider.calls == [["lake fishing"]]


def test_query_is_stripped():
    provider = _FakeProvider()
    semantic.embed_query("  lake fishing \n", provider=provider)
    assert provider.calls == [["lake fishing"]]


def test_embedding_failure_degrades_to_none():
    """A dead embedding service must not raise out of the search path."""
    provider = _FakeProvider(error=EmbeddingError("service down"))
    assert semantic.embed_query("lake", provider=provider) is None


def test_unexpected_provider_error_also_degrades():
    """Not just EmbeddingError: anything the provider throws is contained."""
    provider = _FakeProvider(error=RuntimeError("socket exploded"))
    assert semantic.embed_query("lake", provider=provider) is None


def test_empty_vector_list_degrades_to_none():
    class _EmptyProvider:
        def embed(self, texts):
            return []

    assert semantic.embed_query("lake", provider=_EmptyProvider()) is None


# ========== search ==========

def test_search_returns_empty_without_touching_the_database():
    """An unembeddable query must not open a connection.

    get_connection is replaced with a bomb; if search reached it, this fails.
    """
    def _bomb():
        raise AssertionError("search must not connect when there is no vector")

    original = semantic.get_connection
    semantic.get_connection = _bomb
    try:
        provider = _FakeProvider(error=EmbeddingError("down"))
        assert semantic.search("lake", provider=provider) == []
    finally:
        semantic.get_connection = original


def test_search_passes_the_vector_as_a_bound_parameter(monkeypatch):
    """The vector is a literal cast in SQL, but still a bound parameter.

    Interpolating it into the statement would be an injection surface and
    would defeat statement caching.
    """
    captured = {}

    class _Cursor:
        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params
        def fetchall(self):
            return [(7, 0.93), (9, 0.81)]
        def close(self):
            pass

    class _Conn:
        def cursor(self):
            return _Cursor()
        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(semantic, "get_connection", lambda: _Conn())

    provider = _FakeProvider(vector=[0.5] * 768)
    results = semantic.search("lake", limit=2, provider=provider)

    assert results == [(7, 0.93), (9, 0.81)]
    assert "%s::vector" in captured["sql"]
    # Three params: the vector twice (select and order by) and the limit.
    assert len(captured["params"]) == 3
    assert captured["params"][2] == 2
    assert captured["params"][0].startswith("[0.5,")
    assert captured["closed"] is True


def test_search_closes_the_connection_on_error(monkeypatch):
    """A leaked connection would be held for the life of the worker."""
    state = {"closed": False}

    class _Cursor:
        def execute(self, sql, params):
            raise RuntimeError("query blew up")
        def close(self):
            pass

    class _Conn:
        def cursor(self):
            return _Cursor()
        def close(self):
            state["closed"] = True

    monkeypatch.setattr(semantic, "get_connection", lambda: _Conn())

    with pytest.raises(RuntimeError):
        semantic.search("lake", provider=_FakeProvider())
    assert state["closed"] is True


def test_search_defaults_its_limit_to_the_config(monkeypatch):
    captured = {}

    class _Cursor:
        def execute(self, sql, params):
            captured["limit"] = params[2]
        def fetchall(self):
            return []
        def close(self):
            pass

    class _Conn:
        def cursor(self):
            return _Cursor()
        def close(self):
            pass

    monkeypatch.setattr(semantic, "get_connection", lambda: _Conn())

    semantic.search("lake", provider=_FakeProvider())
    # Literal, not DEFAULT_CONFIG.semantic_candidates: comparing the default
    # against itself passes no matter what the default is changed to.
    assert captured["limit"] == 200
    assert DEFAULT_CONFIG.semantic_candidates == 200

    narrow = DEFAULT_CONFIG.replace(semantic_candidates=11)
    semantic.search("lake", config=narrow, provider=_FakeProvider())
    assert captured["limit"] == 11


def test_knn_sql_orders_on_the_distance_operator():
    """Ordering on '1 - (...)' descending is algebraically identical but the
    planner will not use the HNSW index for it. Pin the index-friendly form.
    """
    assert "ORDER BY embedding <=> %s::vector" in semantic._KNN_SQL


def test_query_provider_is_configured_to_fail_fast():
    """The batch defaults (300s, 3 retries with backoff) turned a dead
    embedding service into a 3-second hang on every search.
    """
    assert semantic.QUERY_MAX_RETRIES == 1
    assert semantic.QUERY_TIMEOUT_SECONDS <= 5
