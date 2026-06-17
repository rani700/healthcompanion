"""Embeddings via Gemini's gemini-embedding-001.

Documents and queries use different task types (RETRIEVAL_DOCUMENT vs
RETRIEVAL_QUERY) — this asymmetric pairing measurably improves retrieval quality.
"""

from __future__ import annotations

import config
from healthcompanion.gemini_client import call_with_retry, get_client

# Max inputs per embed_content request. The API caps batch size, so a long
# multi-page document (many chunks) must be embedded in batches or it fails.
_EMBED_BATCH = 100


def _embed(texts: list[str], task_type: str) -> list[list[float]]:
    if not texts:
        return []
    client = get_client()
    from google.genai import types

    out: list[list[float]] = []
    for start in range(0, len(texts), _EMBED_BATCH):
        batch = texts[start:start + _EMBED_BATCH]
        result = call_with_retry(
            lambda b=batch: client.models.embed_content(
                model=config.MODEL_EMBED,
                contents=b,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=config.EMBED_DIM,
                ),
            )
        )
        out.extend(e.values for e in result.embeddings)
    return out


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed stored document chunks."""
    return _embed(texts, "RETRIEVAL_DOCUMENT")


def embed_query(text: str) -> list[float]:
    """Embed a single search query."""
    return _embed([text], "RETRIEVAL_QUERY")[0]
