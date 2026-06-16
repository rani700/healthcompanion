"""Tests for the RAG improvements: MMR, relevance gate, follow-up context."""

from __future__ import annotations

from healthcompanion import patients, rag, vectorstore


def test_mmr_diversifies():
    # "a" and "a-dup" are near-identical; "b" is also relevant but distinct.
    q = [1.0, 0.0, 0.0]
    cands = [
        {"embedding": [1.0, 0.0, 0.0], "text": "a", "distance": 0.10, "doc_id": "1"},
        {"embedding": [1.0, 0.0, 0.0], "text": "a-dup", "distance": 0.10, "doc_id": "1"},
        {"embedding": [0.7, 0.7, 0.0], "text": "b", "distance": 0.30, "doc_id": "2"},
    ]
    # Diversity-leaning lambda: the near-duplicate is dropped in favour of "b".
    sel = vectorstore._mmr_select(q, cands, 2, 0.3)
    texts = [c["text"] for c in sel]
    assert texts[0] == "a"          # most relevant chosen first
    assert "b" in texts and "a-dup" not in texts


def test_mmr_relevance_leaning_keeps_best():
    # With the default (relevance-leaning) lambda, MMR preserves top relevance.
    q = [1.0, 0.0, 0.0]
    cands = [
        {"embedding": [1.0, 0.0, 0.0], "text": "a", "distance": 0.10, "doc_id": "1"},
        {"embedding": [0.0, 1.0, 0.0], "text": "irrelevant", "distance": 0.9, "doc_id": "2"},
    ]
    sel = vectorstore._mmr_select(q, cands, 1, 0.6)
    assert sel[0]["text"] == "a"


def test_relevance_gate_blocks_far_matches(monkeypatch):
    monkeypatch.setattr(patients, "get_patient", lambda pid: {"id": pid})
    monkeypatch.setattr(rag, "embed_query", lambda t: [0.0])
    # Closest chunk is farther than the threshold -> honest "not found".
    monkeypatch.setattr(
        vectorstore, "candidates",
        lambda *a, **k: [{"distance": 0.95, "text": "x", "doc_id": "1",
                          "doc_type": "rx", "doc_date": "", "filename": "f"}],
    )

    def _no_gen():
        raise AssertionError("generation must not run when gated")

    monkeypatch.setattr(rag, "get_client", _no_gen)
    out = rag.ask("p", "completely unrelated question")
    assert out["used_chunks"] == 0 and "couldn't find" in out["answer"].lower()


def test_overview_question_uses_whole_record(monkeypatch):
    """Broad questions summarize the whole record instead of narrow retrieval."""
    monkeypatch.setattr(patients, "get_patient", lambda pid: {"id": pid})
    called = {}

    def _all(pid, visit_id=None, doc_ids=None):
        called["all"] = True
        return [{"text": "HbA1c 8.1%", "doc_type": "lab", "doc_date": "2024", "filename": "f"}]

    monkeypatch.setattr(vectorstore, "get_all_chunks", _all)

    def _no_retrieval(*a, **k):
        raise AssertionError("overview must not use narrow retrieval")

    monkeypatch.setattr(vectorstore, "candidates", _no_retrieval)

    class _Resp:
        text = "Recent results: HbA1c 8.1% (source: f, 2024)."

    class _Models:
        def generate_content(self, *a, **k):
            return _Resp()

    class _Client:
        models = _Models()

    monkeypatch.setattr(rag, "get_client", lambda: _Client())
    out = rag.ask("p", "what is my medical history?")
    assert called.get("all") and "HbA1c" in out["answer"] and out["used_chunks"] == 1


def test_history_contextualizes_retrieval(monkeypatch):
    monkeypatch.setattr(patients, "get_patient", lambda pid: {"id": pid})
    captured = {}

    def _embed(t):
        captured["q"] = t
        return [0.0]

    monkeypatch.setattr(rag, "embed_query", _embed)
    monkeypatch.setattr(vectorstore, "candidates", lambda *a, **k: [])  # not found, no gen
    rag.ask(
        "p", "what's the dosage?",
        history=[{"role": "user", "text": "which medicine at night"},
                 {"role": "assistant", "text": "Aspirin at night"}],
    )
    # The follow-up retrieval query carries the previous question's context.
    assert "which medicine at night" in captured["q"] and "dosage" in captured["q"]
