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


def ingest_text(
    patient_id: str,
    text: str,
    filename: str,
    doc_type: str = "other",
    doc_date: str | None = None,
    visit_id: str | None = None,
    uploaded_by: str | None = None,
) -> dict[str, Any]:
    """Ingest already-known text (e.g. a doctor-composed prescription) — no file,
    no OCR, no medical-document guardrail (it's authored in-app). Chunks, embeds,
    stores, and registers it like any other document."""
    if patients.get_patient(patient_id) is None:
        raise ValueError(f"Unknown patient: {patient_id}")
    chunks = chunk_text(text)
    if not chunks:
        raise RuntimeError("Nothing to ingest (empty text).")
    header = f"[{doc_type} · {doc_date or 'undated'} · {filename}]"
    embeddings = embed_documents([f"{header}\n{c}" for c in chunks])
    doc_id = uuid.uuid4().hex[:12]
    vectorstore.add_chunks(
        patient_id=patient_id, doc_id=doc_id, chunks=chunks, embeddings=embeddings,
        doc_type=doc_type, doc_date=doc_date, filename=filename, visit_id=visit_id,
    )
    try:
        patients.add_document(
            patient_id=patient_id, filename=filename, doc_type=doc_type,
            doc_date=doc_date, n_chunks=len(chunks), doc_id=doc_id,
            visit_id=visit_id, uploaded_by=uploaded_by,
        )
    except Exception:
        vectorstore.delete_doc_chunks(patient_id, doc_id)
        raise
    return {"doc_id": doc_id, "n_chunks": len(chunks), "filename": filename,
            "doc_type": doc_type, "doc_date": doc_date}


def ingest_document(
    patient_id: str,
    path: str | Path,
    doc_type: str = "other",
    doc_date: str | None = None,
    visit_id: str | None = None,
    uploaded_by: str | None = None,
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

    # Embed a small header (type · date · filename) with each chunk so date- and
    # type-oriented questions ("what was prescribed in 2024?") retrieve better.
    # The stored chunk text stays clean; only the embedded text carries the header.
    header = f"[{doc_type} · {doc_date or 'undated'} · {filename}]"
    embeddings = embed_documents([f"{header}\n{c}" for c in chunks])

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
    # Register in the catalog with the same doc_id used for the vectors. If this
    # fails, roll back the vector chunks so we don't leave orphaned embeddings.
    try:
        patients.add_document(
            patient_id=patient_id,
            filename=filename,
            doc_type=doc_type,
            doc_date=doc_date,
            n_chunks=len(chunks),
            doc_id=doc_id,
            visit_id=visit_id,
            uploaded_by=uploaded_by,
        )
    except Exception:
        vectorstore.delete_doc_chunks(patient_id, doc_id)
        raise

    return {
        "doc_id": doc_id,
        "n_chunks": len(chunks),
        "filename": filename,
        "doc_type": doc_type,
        "doc_date": doc_date,
    }
