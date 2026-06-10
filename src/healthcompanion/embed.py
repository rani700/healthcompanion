"""Embeddings via Gemini's gemini-embedding-001.

Documents and queries use different task types (RETRIEVAL_DOCUMENT vs
RETRIEVAL_QUERY) — this asymmetric pairing measurably improves retrieval quality.
"""

from __future__ import annotations

import config
from healthcompanion.gemini_client import call_with_retry, get_client


def _embed(texts: list[str], task_type: str) -> list[list[float]]:
    if not texts:
        return []
    client = get_client()
    from google.genai import types

    result = call_with_retry(
        lambda: client.models.embed_content(
            model=config.MODEL_EMBED,
            contents=texts,
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=config.EMBED_DIM,
            ),
        )
    )
    return [e.values for e in result.embeddings]


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed stored document chunks."""
    return _embed(texts, "RETRIEVAL_DOCUMENT")


def embed_query(text: str) -> list[float]:
    """Embed a single search query."""
    return _embed([text], "RETRIEVAL_QUERY")[0]
