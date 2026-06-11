"""Tests for the medical-document guardrail."""

from __future__ import annotations

import pytest

from healthcompanion import guardrails
from healthcompanion.guardrails import NotMedicalDocument, assert_medical


def test_assert_medical_allows_medical(monkeypatch):
    monkeypatch.setattr(
        guardrails, "classify_document",
        lambda text: {"medical": True, "type": "lab", "reason": "lab report"},
    )
    result = assert_medical("Hemoglobin 13.5 g/dL ...")
    assert result["type"] == "lab"


def test_assert_medical_blocks_non_medical(monkeypatch):
    monkeypatch.setattr(
        guardrails, "classify_document",
        lambda text: {"medical": False, "type": "non_medical", "reason": "marksheet"},
    )
    with pytest.raises(NotMedicalDocument) as exc:
        assert_medical("Mathematics 95  Physics 88  Chemistry 91")
    assert "doesn't look like a medical document" in str(exc.value)


def test_ingest_rejects_non_medical(monkeypatch, tmp_path):
    """A non-medical file must never reach chunking/embedding/storage."""
    import config
    from healthcompanion import patients
    from healthcompanion import ingest as ingest_mod

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "p.db")

    pid = patients.create_patient("Guard Test")
    doc = tmp_path / "marksheet.txt"
    doc.write_text("Report Card — Maths 95, Physics 88", encoding="utf-8")

    # Classifier says non-medical; embedding/storage should never be invoked.
    monkeypatch.setattr(
        guardrails, "classify_document",
        lambda text: {"medical": False, "type": "non_medical", "reason": "marksheet"},
    )

    def _boom(*a, **k):
        raise AssertionError("non-medical doc must not be embedded")

    monkeypatch.setattr(ingest_mod, "embed_documents", _boom)

    with pytest.raises(NotMedicalDocument):
        ingest_mod.ingest_document(pid, doc, doc_type="rx")
