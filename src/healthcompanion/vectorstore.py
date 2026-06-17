"""Per-patient vector storage backed by ChromaDB.

Isolation is defense-in-depth: each patient gets a dedicated collection
(``patient_{id}``) AND every query carries a ``patient_id`` metadata filter, so a
search can never reach another patient's chunks.

We supply our own Gemini embeddings (Chroma's default embedder is disabled).
"""

from __future__ import annotations

import math
import re
from typing import Any

import chromadb

import config

_client = None

_RRF_K = 60  # Reciprocal Rank Fusion constant
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "what", "which", "who", "when",
    "how", "do", "does", "did", "i", "my", "of", "to", "at", "in", "on", "and",
    "or", "for", "with", "me", "you", "this", "that", "any", "have", "has", "had",
    "should", "would", "could", "can", "be", "am", "it", "about", "tell",
}


def _keywords(text: str) -> list[str]:
    """Salient query terms for keyword matching (drug names, codes, numbers)."""
    toks = re.findall(r"[A-Za-z0-9]+", (text or "").lower())
    out = []
    for t in toks:
        if any(ch.isdigit() for ch in t) or (len(t) >= 3 and t not in _STOPWORDS):
            if t not in out:
                out.append(t)
    return out[:8]


def _where(patient_id, visit_id=None, doc_ids=None):
    conds: list[dict] = [{"patient_id": patient_id}]
    if visit_id:
        conds.append({"visit_id": visit_id})
    if doc_ids is not None:
        conds.append({"doc_id": {"$in": list(doc_ids)}})
    return conds[0] if len(conds) == 1 else {"$and": conds}


def _contains_filter(terms: list[str]):
    """Chroma where_document filter matching any term (case variants)."""
    clauses = []
    for t in terms:
        for v in dict.fromkeys([t, t.capitalize(), t.upper()]):
            clauses.append({"$contains": v})
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$or": clauses}


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _mmr_select(query_emb, cands: list[dict], k: int, lam: float) -> list[dict]:
    """Maximal Marginal Relevance: pick k candidates balancing relevance to the
    query against diversity from already-picked ones."""
    rel = [_cosine(query_emb, c["embedding"]) for c in cands]
    selected: list[int] = []
    remaining = list(range(len(cands)))
    while remaining and len(selected) < k:
        best_i, best_score = remaining[0], -1e9
        for i in remaining:
            if not selected:
                score = rel[i]
            else:
                max_sim = max(
                    _cosine(cands[i]["embedding"], cands[j]["embedding"]) for j in selected
                )
                score = lam * rel[i] - (1 - lam) * max_sim
            if score > best_score:
                best_score, best_i = score, i
        selected.append(best_i)
        remaining.remove(best_i)
    return [cands[i] for i in selected]


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


def delete_collection(patient_id: str) -> None:
    """Drop a patient's entire vector collection (used when purging a patient)."""
    try:
        _get_client().delete_collection(name=f"patient_{patient_id}")
    except Exception:
        pass  # already gone / never created


def delete_doc_chunks(patient_id: str, doc_id: str) -> int:
    """Remove all of a document's chunks from the patient's collection."""
    col = _collection(patient_id)
    res = col.get(where={"doc_id": doc_id})
    ids = res.get("ids") or []
    if ids:
        col.delete(ids=ids)
    return len(ids)


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


def get_all_chunks(
    patient_id: str, limit: int = 60, visit_id: str | None = None, doc_ids=None
):
    """Return up to ``limit`` stored chunks for a patient (for summarization).

    Pass ``visit_id`` to restrict to one visit, and ``doc_ids`` to restrict to a
    set of documents (privacy scoping for a doctor's view).
    """
    if doc_ids is not None and not doc_ids:
        return []
    col = _collection(patient_id)
    if col.count() == 0:
        return []
    where = _where(patient_id, visit_id, doc_ids)
    # Fetch all matching chunks (Chroma `get` has no ordering), then sort by date
    # NEWEST-FIRST and cap. This keeps the most recent documents when a record is
    # larger than `limit` — Chroma's own (insertion) order would otherwise drop
    # exactly the latest docs before any date sort.
    res = col.get(include=["documents", "metadatas"], where=where)
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
    out.sort(key=lambda c: c["doc_date"] or "", reverse=True)
    return out[:limit]


def _cand(doc, meta, emb) -> dict:
    meta = meta or {}
    return {
        "text": doc,
        "doc_id": meta.get("doc_id", ""),
        "doc_type": meta.get("doc_type", ""),
        "doc_date": meta.get("doc_date", ""),
        "filename": meta.get("filename", ""),
        "embedding": list(emb),
    }


def candidates(
    patient_id: str,
    query_embedding: list[float],
    query_text: str | None = None,
    visit_id: str | None = None,
    doc_ids=None,
    n: int | None = None,
):
    """Hybrid retrieval: fuse vector (semantic) and keyword (exact-term) matches
    via Reciprocal Rank Fusion. Returns a candidate pool (each with ``embedding``
    and a unified cosine ``distance``) for the caller to MMR/rerank into top_k.

    Constrained to the patient; ANDed with ``visit_id`` and/or an allowed
    ``doc_ids`` set (privacy scoping) when given.
    """
    if doc_ids is not None and not doc_ids:
        return []
    col = _collection(patient_id)
    count = col.count()
    if count == 0:
        return []
    n = min(n or config.RAG_CANDIDATES, count)
    where = _where(patient_id, visit_id, doc_ids)

    pool: dict[str, dict] = {}

    # 1) Vector (semantic) results — ranked by similarity.
    vres = col.query(
        query_embeddings=[query_embedding],
        n_results=n,
        where=where,
        include=["documents", "metadatas", "embeddings"],
    )
    for rank, (id_, doc, meta, emb) in enumerate(
        zip(vres["ids"][0], vres["documents"][0], vres["metadatas"][0], vres["embeddings"][0])
    ):
        c = _cand(doc, meta, emb)
        c["_vrank"], c["_krank"] = rank, None
        pool[id_] = c

    # 2) Keyword (exact-term) results — ranked by number of query terms present.
    terms = _keywords(query_text or "")
    wd = _contains_filter(terms)
    if wd is not None:
        kres = col.get(
            where=where, where_document=wd, limit=n,
            include=["documents", "metadatas", "embeddings"],
        )
        scored = []
        for id_, doc, meta, emb in zip(
            kres["ids"], kres["documents"], kres["metadatas"], kres["embeddings"]
        ):
            hits = sum(1 for t in terms if t in (doc or "").lower())
            scored.append((hits, id_, doc, meta, emb))
        scored.sort(key=lambda x: -x[0])
        for krank, (_, id_, doc, meta, emb) in enumerate(scored):
            if id_ in pool:
                pool[id_]["_krank"] = krank
            else:
                c = _cand(doc, meta, emb)
                c["_vrank"], c["_krank"] = None, krank
                pool[id_] = c

    # 3) Reciprocal Rank Fusion + a unified cosine distance for the relevance gate.
    for c in pool.values():
        score = 0.0
        if c["_vrank"] is not None:
            score += 1.0 / (_RRF_K + c["_vrank"])
        if c["_krank"] is not None:
            score += 1.0 / (_RRF_K + c["_krank"])
        c["_rrf"] = score
        c["distance"] = 1.0 - _cosine(query_embedding, c["embedding"])

    fused = sorted(pool.values(), key=lambda c: -c["_rrf"])
    return fused[:n]
