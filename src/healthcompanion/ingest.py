"""Ingestion pipeline: extract -> chunk -> embed -> store -> register."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from healthcompanion import patients, vectorstore
from healthcompanion.chunk import chunk_text
from healthcompanion.embed import embed_documents
from healthcompanion.extract import extract_text
from healthcompanion.guardrails import assert_medical


def ingest_document(
    patient_id: str,
    path: str | Path,
    doc_type: str = "other",
    doc_date: str | None = None,
    visit_id: str | None = None,
) -> dict[str, Any]:
    """Ingest one document for a patient.

    Returns a summary dict: doc_id, n_chunks, filename, doc_type, doc_date.
    Raises if the patient doesn't exist.
    """
    if patients.get_patient(patient_id) is None:
        raise ValueError(f"Unknown patient: {patient_id}")

    path = Path(path)
    filename = path.name

    text = extract_text(path)

    # Guardrail: only genuine medical documents may enter a patient record.
    assert_medical(text)

    chunks = chunk_text(text)
    if not chunks:
        raise RuntimeError(f"No text extracted from {filename}")

    embeddings = embed_documents(chunks)

    # Allocate the doc_id up front so vector ids and the catalog row agree.
    doc_id = uuid.uuid4().hex[:12]
    vectorstore.add_chunks(
        patient_id=patient_id,
        doc_id=doc_id,
        chunks=chunks,
        embeddings=embeddings,
        doc_type=doc_type,
        doc_date=doc_date,
        filename=filename,
        visit_id=visit_id,
    )
    # Register in the catalog with the same doc_id used for the vectors.
    patients.add_document(
        patient_id=patient_id,
        filename=filename,
        doc_type=doc_type,
        doc_date=doc_date,
        n_chunks=len(chunks),
        doc_id=doc_id,
        visit_id=visit_id,
    )

    return {
        "doc_id": doc_id,
        "n_chunks": len(chunks),
        "filename": filename,
        "doc_type": doc_type,
        "doc_date": doc_date,
    }
