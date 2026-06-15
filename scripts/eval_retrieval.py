"""Retrieval evaluation harness.

Builds an index from eval/dataset.json and runs each query through the REAL
retrieval path (hybrid candidates -> MMR top_k), checking whether expected terms
appear in the retrieved chunks. Reports hit-rate. Uses Gemini embeddings only.

    HC_DATA_DIR=/tmp/hc-eval .venv/bin/python scripts/eval_retrieval.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import config
from healthcompanion.chunk import chunk_text
from healthcompanion.embed import embed_documents, embed_query
from healthcompanion import vectorstore

PATIENT = "eval-patient"


def index_documents(docs):
    for i, d in enumerate(docs):
        chunks = chunk_text(d["text"])
        header = f"[{d['doc_type']} · {d.get('doc_date', 'undated')} · doc{i}]"
        embs = embed_documents([f"{header}\n{c}" for c in chunks])
        vectorstore.add_chunks(
            PATIENT, f"doc{i}", chunks, embs, d["doc_type"],
            d.get("doc_date"), f"doc{i}", visit_id=None,
        )
    print(f"indexed {len(docs)} document(s)")


def retrieve(q):
    qv = embed_query(q)
    cands = vectorstore.candidates(PATIENT, qv, query_text=q)
    if not cands:
        return [], 1.0
    best = min(c["distance"] for c in cands)
    hits = vectorstore._mmr_select(qv, cands, config.TOP_K, config.RAG_MMR_LAMBDA)
    return hits, best


def main():
    data = json.loads((ROOT / "eval" / "dataset.json").read_text())
    index_documents(data["documents"])
    print()

    answerable = [q for q in data["queries"] if not q.get("expect_none")]
    hits_count = 0
    for q in data["queries"]:
        chunks, best = retrieve(q["q"])
        blob = " ".join(c["text"].lower() for c in chunks)
        if q.get("expect_none"):
            gated = best > config.RAG_MAX_DISTANCE
            print(f"[probe ] best_dist={best:.3f} gated={gated} | {q['q']}")
            continue
        found = [t for t in q["expect_any"] if t.lower() in blob]
        ok = bool(found)
        hits_count += ok
        mark = "HIT " if ok else "MISS"
        print(f"[{mark}] best_dist={best:.3f} found={found} | {q['q']}")

    n = len(answerable)
    print(f"\nHit-rate: {hits_count}/{n} = {hits_count / n:.0%}  (top_k={config.TOP_K})")


if __name__ == "__main__":
    main()
