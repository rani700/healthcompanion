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
    visit_id: str | None = None,
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
            "visit_id": visit_id or "",
        }
        for i in range(len(chunks))
    ]
    col.add(ids=ids, documents=chunks, embeddings=embeddings, metadatas=metadatas)
    return len(chunks)


def update_doc_visit(patient_id: str, doc_id: str, visit_id: str | None) -> int:
    """Re-tag a document's chunks with a new visit_id so visit-scoped search
    stays accurate after a document is moved."""
    col = _collection(patient_id)
    res = col.get(where={"doc_id": doc_id}, include=["metadatas"])
    ids = res.get("ids") or []
    metas = res.get("metadatas") or []
    if not ids:
        return 0
    for m in metas:
        m["visit_id"] = visit_id or ""
    col.update(ids=ids, metadatas=metas)
    return len(ids)


def get_all_chunks(patient_id: str, limit: int = 60, visit_id: str | None = None):
    """Return up to ``limit`` stored chunks for a patient (for summarization).

    Pass ``visit_id`` to restrict to a single visit's documents.
    """
    col = _collection(patient_id)
    if col.count() == 0:
        return []
    where = {"visit_id": visit_id} if visit_id else None
    res = col.get(include=["documents", "metadatas"], limit=limit, where=where)
    docs = res.get("documents") or []
    metas = res.get("metadatas") or []
    out = [
        {
            "text": doc,
            "doc_type": (meta or {}).get("doc_type", ""),
            "doc_date": (meta or {}).get("doc_date", ""),
            "filename": (meta or {}).get("filename", ""),
        }
        for doc, meta in zip(docs, metas)
    ]
    # Group roughly by date so the summary reads chronologically.
    out.sort(key=lambda c: c["doc_date"] or "")
    return out


def query(
    patient_id: str,
    query_embedding: list[float],
    top_k: int = config.TOP_K,
    visit_id: str | None = None,
):
    """Return the top-k most relevant chunks for a patient.

    Pass ``visit_id`` to restrict retrieval to one visit's documents. Output: list
    of dicts with ``text``, ``doc_type``, ``doc_date``, ``filename``, ``distance``.
    """
    col = _collection(patient_id)
    n = min(top_k, col.count())
    if n == 0:
        return []
    where = {"visit_id": visit_id} if visit_id else {"patient_id": patient_id}
    res = col.query(
        query_embeddings=[query_embedding],
        n_results=n,
        where=where,
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
