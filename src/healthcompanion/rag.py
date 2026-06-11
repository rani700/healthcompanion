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
) -> dict[str, Any]:
    """Answer a question about one patient's records.

    Returns {answer, sources, used_chunks}. Raises if the patient is unknown.
    """
    if patients.get_patient(patient_id) is None:
        raise ValueError(f"Unknown patient: {patient_id}")

    role = role if role in _ROLE_GUIDANCE else "patient"

    q_vec = embed_query(question)
    hits = vectorstore.query(patient_id, q_vec, top_k=top_k)

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


def _dedupe_sources(hits: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen = set()
    out = []
    for h in hits:
        key = (h["filename"], h["doc_date"])
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
