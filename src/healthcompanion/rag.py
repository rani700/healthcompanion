"""Retrieval-augmented question answering, scoped to a single patient.

retrieve -> build grounded context -> generate (role-aware, with citations).
"""

from __future__ import annotations

import json
from typing import Any

import config
from healthcompanion import patients, vectorstore
from healthcompanion.embed import embed_query
from healthcompanion.gemini_client import call_with_retry, get_client


def _llm_rerank(question: str, cands: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    """Re-rank candidates by asking the model which excerpts best answer the
    question (a pragmatic cross-encoder). Falls back to input order on any error."""
    listing = "\n".join(f"[{i}] {c['text'][:400]}" for i, c in enumerate(cands))
    prompt = (
        f"Question: {question}\n\nExcerpts:\n{listing}\n\n"
        f"Return ONLY a JSON array of excerpt numbers most relevant to answering "
        f"the question, best first, at most {top_k} items."
    )
    client = get_client()
    from google.genai import types

    try:
        resp = call_with_retry(
            lambda: client.models.generate_content(
                model=config.MODEL_GEN,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0, response_mime_type="application/json"
                ),
            )
        )
        order = json.loads((resp.text or "").strip())
        idxs = [int(i) for i in order if 0 <= int(i) < len(cands)]
    except Exception:
        idxs = []
    if not idxs:
        return cands[:top_k]
    seen, picked = set(), []
    for i in idxs + list(range(len(cands))):  # fill any gap with remaining order
        if i not in seen:
            seen.add(i)
            picked.append(cands[i])
    return picked[:top_k]

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


_NOT_FOUND = "I couldn't find that in your records."


def ask(
    patient_id: str,
    question: str,
    role: str = "patient",
    top_k: int = config.TOP_K,
    visit_id: str | None = None,
    history: list[dict[str, str]] | None = None,
    doc_ids=None,
) -> dict[str, Any]:
    """Answer a question about one patient's records.

    Pass ``visit_id`` to restrict to one visit, ``doc_ids`` to restrict to an
    allowed document set (privacy scoping), and ``history`` (recent {role, text}
    turns, never stored) for follow-ups. Returns {answer, sources, used_chunks}.
    """
    if patients.get_patient(patient_id) is None:
        raise ValueError(f"Unknown patient: {patient_id}")

    role = role if role in _ROLE_GUIDANCE else "patient"
    history = (history or [])[-config.RAG_HISTORY_TURNS:]

    # Contextualize the retrieval query with the previous user turn so follow-ups
    # like "what's the dosage?" still retrieve the right chunks.
    prev_user = next((m["text"] for m in reversed(history) if m.get("role") == "user"), "")
    retrieval_text = f"{prev_user} {question}".strip() if prev_user else question

    q_vec = embed_query(retrieval_text)
    cands = vectorstore.candidates(
        patient_id, q_vec, query_text=retrieval_text, visit_id=visit_id, doc_ids=doc_ids
    )

    # Nothing stored, or even the closest chunk is too far -> honest not-found.
    if not cands or min(c["distance"] for c in cands) > config.RAG_MAX_DISTANCE:
        return {"answer": _NOT_FOUND, "sources": [], "used_chunks": 0}

    # Select top_k: opt-in LLM re-rank for sharper ordering, else MMR for diversity.
    if config.RAG_RERANK:
        hits = _llm_rerank(question, cands, top_k)
    else:
        hits = vectorstore._mmr_select(q_vec, cands, top_k, config.RAG_MMR_LAMBDA)
    for h in hits:
        h.pop("embedding", None)

    system = _SYSTEM_TEMPLATE.format(
        role_guidance=_ROLE_GUIDANCE[role],
        context=_build_context(hits),
    )

    convo = "".join(
        f"{'User' if m.get('role') == 'user' else 'Assistant'}: {m.get('text','')}\n"
        for m in history
    )
    user_content = (
        (f"Earlier in this conversation:\n{convo}\n" if convo else "")
        + f"Question: {question}"
    )

    client = get_client()
    from google.genai import types

    response = call_with_retry(
        lambda: client.models.generate_content(
            model=config.MODEL_GEN,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.2,
            ),
        )
    )

    return {
        "answer": (response.text or "").strip(),
        "sources": _dedupe_sources(hits),
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


def summarize_patient(
    patient_id: str, refresh: bool = False, doc_ids=None
) -> dict[str, Any]:
    """Generate a short clinical summary from a patient's stored records.

    The patient's full summary is cached (regenerated when documents change). A
    doctor-scoped summary (``doc_ids`` given) is generated fresh over only the
    visible documents and is never cached (to avoid leaking the full record).
    """
    if patients.get_patient(patient_id) is None:
        raise ValueError(f"Unknown patient: {patient_id}")

    scoped = doc_ids is not None
    if not scoped:
        sig = patients.docs_fingerprint(patient_id)
        if not refresh:
            cached = patients.get_cached_summary(patient_id)
            if cached and cached[0] and cached[1] == sig:
                return {"summary": cached[0], "has_records": True, "cached": True}

    chunks = vectorstore.get_all_chunks(patient_id, doc_ids=doc_ids)
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
    if not scoped:
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
