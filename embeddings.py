"""Embedding provider interface for generating vector embeddings.

Supports OpenAI-compatible embedding services (nomic-embed-text, OpenAI, etc.).
Configuration is driven by environment variables so that development uses
a local llama.cpp service and production uses a hosted endpoint without
code changes.

Environment variables:
    EMBEDDINGS_BASE_URL (default: http://localhost:8000/v1)
    EMBEDDINGS_MODEL (default: nomic-embed-text)
    EMBEDDINGS_TIMEOUT (default: 300)
    EMBEDDINGS_MAX_RETRIES (default: 3)
"""

import os
import time
import requests


class EmbeddingError(Exception):
    """Raised when embedding generation fails."""
    pass


class EmbeddingDimensionError(EmbeddingError):
    """Raised when the embedding service returns an unexpected dimension."""
    pass


class EmbeddingProvider:
    """Interface to an OpenAI-compatible embedding service."""

    def __init__(self):
        self.base_url = os.environ.get(
            "EMBEDDINGS_BASE_URL", "http://localhost:8000/v1"
        ).rstrip("/")
        self.model = os.environ.get("EMBEDDINGS_MODEL", "nomic-embed-text")
        self.timeout = int(os.environ.get("EMBEDDINGS_TIMEOUT", "300"))
        self.max_retries = int(os.environ.get("EMBEDDINGS_MAX_RETRIES", "3"))
        self.expected_dimension = 768

    def embed(self, texts):
        """Generate embeddings for a list of texts.

        Args:
            texts: List of strings to embed.

        Returns:
            List of embedding vectors (each a list of floats).

        Raises:
            EmbeddingError: On transient failure after retries.
            EmbeddingDimensionError: If the service returns wrong dimension.
        """
        if not texts:
            return []

        url = f"{self.base_url}/embeddings"
        payload = {"model": self.model, "input": texts}

        backoff = 1.0
        last_error = None

        for attempt in range(self.max_retries):
            try:
                response = requests.post(url, json=payload, timeout=self.timeout)

                if response.status_code == 429:
                    # Rate limit: back off and retry
                    time.sleep(backoff)
                    backoff *= 2
                    continue

                response.raise_for_status()
                data = response.json()

                # Extract embeddings and validate dimension
                embeddings = [item["embedding"] for item in data.get("data", [])]

                if embeddings and len(embeddings[0]) != self.expected_dimension:
                    raise EmbeddingDimensionError(
                        f"Expected {self.expected_dimension}-dimensional embeddings, "
                        f"got {len(embeddings[0])}-dimensional"
                    )

                return embeddings

            except requests.exceptions.Timeout as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2
            except requests.exceptions.ConnectionError as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2
            except EmbeddingDimensionError:
                raise

        raise EmbeddingError(f"Embedding failed after {self.max_retries} retries: {last_error}")
