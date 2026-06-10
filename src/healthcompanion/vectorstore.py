"""Per-patient vector storage backed by ChromaDB.

Isolation is defense-in-depth: each patient gets a dedicated collection
(``patient_{id}``) AND every query carries a ``patient_id`` metadata filter, so a
search can never reach another patient's chunks.

We supply our own Gemini embeddings (Chroma's default embedder is disabled).
"""

from __future__ import annotations

from typing import Any

import chromadb

import config

_client = None


def _get_client():
    global _client
    if _client is None:
        config.ensure_dirs()
        _client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    return _client


def _collection(patient_id: str):
    # We always pass precomputed Gemini embeddings to add()/query(), so Chroma's
    # default embedding function is never invoked (no model is ever downloaded).
    return _get_client().get_or_create_collection(
        name=f"patient_{patient_id}",
        metadata={"hnsw:space": "cosine"},
    )


def add_chunks(
    patient_id: str,
    doc_id: str,
    chunks: list[str],
    embeddings: list[list[float]],
    doc_type: str,
    doc_date: str | None,
    filename: str,
) -> int:
    """Store a document's chunks (with embeddings + metadata) for a patient."""
    col = _collection(patient_id)
    ids = [f"{doc_id}:{i}" for i in range(len(chunks))]
    metadatas: list[dict[str, Any]] = [
        {
            "patient_id": patient_id,
            "doc_id": doc_id,
            "doc_type": doc_type,
            "doc_date": doc_date or "",
            "filename": filename,
            "chunk_index": i,
        }
        for i in range(len(chunks))
    ]
    col.add(ids=ids, documents=chunks, embeddings=embeddings, metadatas=metadatas)
    return len(chunks)


def query(patient_id: str, query_embedding: list[float], top_k: int = config.TOP_K):
    """Return the top-k most relevant chunks for a patient.

    Output: list of dicts with ``text``, ``doc_type``, ``doc_date``, ``filename``,
    and ``distance``.
    """
    col = _collection(patient_id)
    n = min(top_k, col.count())
    if n == 0:
        return []
    res = col.query(
        query_embeddings=[query_embedding],
        n_results=n,
        where={"patient_id": patient_id},  # redundant with per-patient collection, by design
        include=["documents", "metadatas", "distances"],
    )
    docs = res["documents"][0]
    metas = res["metadatas"][0]
    dists = res["distances"][0]
    return [
        {
            "text": doc,
            "doc_type": meta.get("doc_type", ""),
            "doc_date": meta.get("doc_date", ""),
            "filename": meta.get("filename", ""),
            "distance": dist,
        }
        for doc, meta, dist in zip(docs, metas, dists)
    ]
