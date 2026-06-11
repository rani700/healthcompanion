"""End-to-end smoke test of the RAG pipeline with all Gemini calls mocked.

Verifies: ingest (extract->chunk->embed->store->register) and ask
(embed->retrieve->generate) wire together, sources are returned, and patients are
strictly isolated. Runs offline — no API key or network needed.
"""

from __future__ import annotations

import re

import pytest


# --- Fake Gemini client ------------------------------------------------------
class _Emb:
    def __init__(self, values):
        self.values = values


class _EmbResult:
    def __init__(self, embeddings):
        self.embeddings = embeddings


class _GenResult:
    def __init__(self, text):
        self.text = text


def _fake_vector(text: str, dim: int) -> list[float]:
    """Deterministic bag-of-words vector so token overlap drives similarity."""
    v = [0.0] * dim
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        v[hash(tok) % dim] += 1.0
    return v


class _FakeModels:
    def __init__(self, captured, dim):
        self.captured = captured
        self.dim = dim

    def embed_content(self, model, contents, config=None):
        return _EmbResult([_Emb(_fake_vector(t, self.dim)) for t in contents])

    def generate_content(self, model, contents, config=None):
        # Capture the grounded prompt so the test can assert the retrieved
        # context actually reached the model.
        self.captured["system"] = getattr(config, "system_instruction", "") or ""
        self.captured["question"] = contents
        return _GenResult("Take Aspirin 75mg at night. (source: rx.txt, 2026-05-01)")


class _FakeClient:
    def __init__(self, captured, dim):
        self.models = _FakeModels(captured, dim)


# --- Fixtures ----------------------------------------------------------------
@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Point storage at a temp dir, reset singletons, and mock the Gemini client."""
    import config
    from healthcompanion import embed, guardrails, rag, vectorstore

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CHROMA_DIR", tmp_path / "chroma")
    monkeypatch.setattr(config, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "patients.db")

    # Reset the cached Chroma client so it uses the temp path.
    monkeypatch.setattr(vectorstore, "_client", None)

    captured: dict = {}
    fake = _FakeClient(captured, config.EMBED_DIM)
    monkeypatch.setattr(embed, "get_client", lambda: fake)
    monkeypatch.setattr(rag, "get_client", lambda: fake)
    # Treat ingested test docs as medical (the classifier is tested separately).
    monkeypatch.setattr(
        guardrails, "classify_document",
        lambda text: {"medical": True, "type": "rx", "reason": "test"},
    )
    return captured


def _write_doc(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# --- Tests -------------------------------------------------------------------
def test_ingest_and_ask(env, tmp_path):
    from healthcompanion import patients
    from healthcompanion.ingest import ingest_document
    from healthcompanion.rag import ask

    pid = patients.create_patient("Jane Doe")
    doc = _write_doc(
        tmp_path,
        "rx.txt",
        "Prescription for Jane Doe.\nTake Aspirin 75mg once at night after dinner.",
    )

    result = ingest_document(pid, doc, doc_type="rx", doc_date="2026-05-01")
    assert result["n_chunks"] >= 1
    assert patients.list_documents(pid)[0]["filename"] == "rx.txt"

    answer = ask(pid, "Which medicine do I take at night?", role="patient")
    assert answer["used_chunks"] >= 1
    assert answer["sources"] and answer["sources"][0]["filename"] == "rx.txt"
    # The retrieved chunk reached the model's grounded prompt.
    assert "Aspirin" in env["system"]


def test_ask_with_no_documents(env):
    from healthcompanion import patients
    from healthcompanion.rag import ask

    pid = patients.create_patient("Empty Patient")
    answer = ask(pid, "What was prescribed?", role="doctor")
    assert answer["used_chunks"] == 0
    assert "couldn't find" in answer["answer"].lower()


def test_patient_isolation(env, tmp_path):
    from healthcompanion import patients
    from healthcompanion.ingest import ingest_document
    from healthcompanion.rag import ask

    a = patients.create_patient("Patient A")
    b = patients.create_patient("Patient B")
    ingest_document(a, _write_doc(tmp_path, "a.txt", "Aspirin 75mg at night."),
                    doc_type="rx", doc_date="2026-05-01")
    ingest_document(b, _write_doc(tmp_path, "b.txt", "Metformin 500mg twice daily."),
                    doc_type="rx", doc_date="2026-05-02")

    res = ask(a, "What medicine at night?", role="patient")
    files = {s["filename"] for s in res["sources"]}
    assert files == {"a.txt"}          # only A's document
    assert "Metformin" not in env["system"]  # B's content never retrieved


def test_unknown_patient_raises(env):
    from healthcompanion.rag import ask

    with pytest.raises(ValueError):
        ask("does-not-exist", "anything")
