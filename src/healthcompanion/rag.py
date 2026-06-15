"""Retrieval-augmented question answering, scoped to a single patient.

retrieve -> build grounded context -> generate (role-aware, with citations).
"""

from __future__ import annotations

from typing import Any

import config
from healthcompanion import patients, vectorstore
from healthcompanion.embed import embed_query
from healthcompanion.gemini_client import call_with_retry, get_client

_ROLE_GUIDANCE = {
    "doctor": (
        "The reader is a CLINICIAN. Use precise medical terminology, include exact "
        "dosages, lab values, units, and dates. Be concise and clinical."
    ),
    "patient": (
        "The reader is the PATIENT, a non-expert. Explain in plain, reassuring "
        "language. Spell out when and how to take each medicine. Avoid jargon; if a "
        "medical term is unavoidable, briefly explain it."
    ),
}

_SYSTEM_TEMPLATE = """You are HealthCompanion, a medical records assistant.

Answer the question using ONLY the patient's record excerpts provided below.
Rules:
- Do NOT use outside knowledge or guess. If the answer is not in the excerpts,
  reply exactly: "I couldn't find that in your records."
- Cite the source for each fact as (source: <filename>, <date>) using the excerpt's
  document type and date.
- Never invent medications, dosages, dates, or values.
- Do not provide diagnoses or new treatment recommendations beyond what the
  records state; for medical decisions, advise consulting a qualified clinician.
- {role_guidance}

Patient record excerpts:
{context}
"""


def _build_context(hits: list[dict[str, Any]]) -> str:
    blocks = []
    for i, h in enumerate(hits, 1):
        date = h["doc_date"] or "undated"
        header = f"[Excerpt {i} — {h['doc_type']}, {h['filename']}, {date}]"
        blocks.append(f"{header}\n{h['text']}")
    return "\n\n".join(blocks)


def ask(
    patient_id: str,
    question: str,
    role: str = "patient",
    top_k: int = config.TOP_K,
    visit_id: str | None = None,
) -> dict[str, Any]:
    """Answer a question about one patient's records.

    Pass ``visit_id`` to restrict the answer to a single visit's documents.
    Returns {answer, sources, used_chunks}. Raises if the patient is unknown.
    """
    if patients.get_patient(patient_id) is None:
        raise ValueError(f"Unknown patient: {patient_id}")

    role = role if role in _ROLE_GUIDANCE else "patient"

    q_vec = embed_query(question)
    hits = vectorstore.query(patient_id, q_vec, top_k=top_k, visit_id=visit_id)

    if not hits:
        return {
            "answer": "I couldn't find that in your records.",
            "sources": [],
            "used_chunks": 0,
        }

    system = _SYSTEM_TEMPLATE.format(
        role_guidance=_ROLE_GUIDANCE[role],
        context=_build_context(hits),
    )

    client = get_client()
    from google.genai import types

    response = call_with_retry(
        lambda: client.models.generate_content(
            model=config.MODEL_GEN,
            contents=question,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.2,
            ),
        )
    )

    sources = _dedupe_sources(hits)
    return {
        "answer": (response.text or "").strip(),
        "sources": sources,
        "used_chunks": len(hits),
    }


_SUMMARY_SYSTEM = """You are a clinical assistant preparing a brief at-a-glance
summary of a patient for a doctor, using ONLY the record excerpts provided.

Produce a concise summary with these sections (omit a section if there's nothing
for it): Active medications, Conditions/diagnoses, Recent results/findings,
Notable notes. Use short bullet points, include dates where available, and cite
nothing you cannot see in the excerpts. Do NOT diagnose or recommend new
treatment. If the records are sparse, say what little is known. Keep it under
180 words."""


def summarize_patient(patient_id: str, refresh: bool = False) -> dict[str, Any]:
    """Generate a short clinical summary from a patient's stored records.

    Cached and only regenerated when the patient's document set changes (or when
    ``refresh`` forces it), so opening a patient doesn't re-run the model.
    """
    if patients.get_patient(patient_id) is None:
        raise ValueError(f"Unknown patient: {patient_id}")

    sig = patients.docs_fingerprint(patient_id)
    if not refresh:
        cached = patients.get_cached_summary(patient_id)
        if cached and cached[0] and cached[1] == sig:
            return {"summary": cached[0], "has_records": True, "cached": True}

    chunks = vectorstore.get_all_chunks(patient_id)
    if not chunks:
        return {"summary": "", "has_records": False, "cached": False}

    context = _build_context(chunks)
    client = get_client()
    from google.genai import types

    response = call_with_retry(
        lambda: client.models.generate_content(
            model=config.MODEL_GEN,
            contents="Summarize this patient's records.",
            config=types.GenerateContentConfig(
                system_instruction=f"{_SUMMARY_SYSTEM}\n\nPatient record excerpts:\n{context}",
                temperature=0.2,
            ),
        )
    )
    summary = (response.text or "").strip()
    patients.set_cached_summary(patient_id, summary, sig)
    return {"summary": summary, "has_records": True, "cached": False}


def summarize_visit(patient_id: str, visit_id: str) -> dict[str, Any]:
    """Summarize just one visit's documents (uncached, generated on demand)."""
    chunks = vectorstore.get_all_chunks(patient_id, visit_id=visit_id)
    if not chunks:
        return {"summary": "", "has_records": False}

    context = _build_context(chunks)
    client = get_client()
    from google.genai import types

    response = call_with_retry(
        lambda: client.models.generate_content(
            model=config.MODEL_GEN,
            contents="Summarize this visit's records.",
            config=types.GenerateContentConfig(
                system_instruction=f"{_SUMMARY_SYSTEM}\n\nVisit record excerpts:\n{context}",
                temperature=0.2,
            ),
        )
    )
    return {"summary": (response.text or "").strip(), "has_records": True}


def _dedupe_sources(hits: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen = set()
    out = []
    for h in hits:
        # Prefer doc_id (distinguishes same-named files); fall back to name+date.
        key = h.get("doc_id") or (h["filename"], h["doc_date"])
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "filename": h["filename"],
                "doc_type": h["doc_type"],
                "doc_date": h["doc_date"],
            }
        )
    return out
