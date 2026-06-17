"""Retrieval-augmented question answering, scoped to a single patient.

retrieve -> build grounded context -> generate (role-aware, with citations).
"""

from __future__ import annotations

import json
import re
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

The excerpts below ARE this patient's own uploaded records — treat them as this
patient's medical record even if a person's name written inside a document differs
(documents may be scanned or mis-labelled; do not refuse over a name mismatch).

Answer the question using ONLY these excerpts.
Rules:
- For broad questions (medical history, current problems, overview, "is anything
  wrong"), SUMMARISE only what is EXPLICITLY written — list the conditions,
  results, and medications the document actually names. Do not refuse just
  because the question is general.
- REPORT, DON'T INTERPRET. State only what the text literally says. Do NOT infer
  a diagnosis, and do NOT turn a raw value, symptom, or abbreviation into a named
  condition. For example: never call an unlabelled number like "204/100" a
  "blood pressure reading" or "high blood pressure" unless the document literally
  labels it as blood pressure; never report a confirmed "kidney stone" from a
  word like "calculi" unless the document says so in words.
- UNCERTAIN SCANS: text marked "[?]" was hard to read on a scan. Treat anything
  marked "[?]" — and any single ambiguous handwritten word — as UNCERTAIN. Say it
  is unclear and suggest opening the original document to confirm; never present
  it as an established fact or diagnosis.
- MEDICATION NAMES: a name transcribed from a scanned or handwritten prescription
  may not be exact. When you report a medicine name, give it exactly as written in
  the records, and add a brief reminder to verify the spelling against the
  original document (the "View" button) — especially if it carries "[?]".
- If the question asks whether the patient HAS a condition, answer "yes" only when
  the records explicitly name that diagnosis. Otherwise say the records do not
  confirm it, describe what is literally written, and suggest verifying against
  the original document.
- Use ONLY the excerpts; never use outside knowledge or invent medications,
  dosages, dates, or values.
- Cite the source for each fact as (source: <filename>, <date>).
- PARTIAL information: if the excerpts mention the subject of the question (e.g.
  a medication) but not the exact detail asked (e.g. how to take it, dose,
  timing, duration), DO NOT refuse. State what the records DO show about it, then
  say that specific detail isn't recorded, and advise confirming with the
  prescribing clinician. Quote any dose/frequency/duration exactly as written
  (e.g. "Paracetamol 500 mg, twice daily").
- Reply exactly "I couldn't find that in your records." ONLY when NOTHING in the
  excerpts relates to the question at all.
- Do not give new diagnoses or treatment beyond what the records state; for medical
  decisions, advise consulting a qualified clinician.
- {role_guidance}

Patient record excerpts:
{context}
"""

# Phrases that clearly ask for the big picture of the whole record.
_OVERVIEW_PHRASES = (
    "summary", "summarise", "summarize", "overview", "medical history",
    "health history", "my history", "patient history", "everything",
    "all my record", "all records", "whole record", "entire record",
    "anything wrong", "what's wrong", "whats wrong", "is anything wrong",
    "my health", "overall health", "her health", "his health", "their health",
    "my record", "my records", "tell me about",
)

# A broad "list all the X" enumeration (X = a class of clinical items).
_ENUM_RE = re.compile(
    r"\b(what|which|list|any|all)\b.*\b("
    r"conditions?|medications?|medicines?|drugs?|prescriptions?|diagnos\w+|"
    r"problems?|allergies|illness\w*|tests?|investigations?|reports?"
    r")\b"
)
# Markers that the question targets a SPECIFIC detail, not the whole record — so
# even with a broad word present, route it through precise retrieval.
_SPECIFIC_HINTS = (
    "dose", "dosage", "how much", "how many", "how should", "how do i take",
    "how to take", "when ", "what time", " mg", " ml", "frequency", "last ",
    "latest", "most recent", "value of", "result of", "for how long",
)


def _is_overview(question: str) -> bool:
    """True only for genuinely broad 'big picture' questions. A specific question
    (one drug's dose, a single value) stays on precise retrieval even if it
    happens to contain a clinical word like 'medication'."""
    q = (question or "").lower()
    if any(p in q for p in _OVERVIEW_PHRASES):
        return True
    if _ENUM_RE.search(q) and not any(h in q for h in _SPECIFIC_HINTS):
        return True
    return False


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

    if _is_overview(question):
        # Broad question -> answer over the whole (scoped) record, not narrow top-k.
        hits = vectorstore.get_all_chunks(patient_id, visit_id=visit_id, doc_ids=doc_ids)
        if not hits:
            return {"answer": _NOT_FOUND, "sources": [], "used_chunks": 0}
    else:
        # Contextualize the retrieval query with the previous user turn so follow-ups
        # like "what's the dosage?" still retrieve the right chunks.
        prev_user = next((m["text"] for m in reversed(history) if m.get("role") == "user"), "")
        retrieval_text = f"{prev_user} {question}".strip() if prev_user else question

        q_vec = embed_query(retrieval_text)
        cands = vectorstore.candidates(
            patient_id, q_vec, query_text=retrieval_text, visit_id=visit_id, doc_ids=doc_ids
        )
        # Nothing stored, or even the closest chunk is too far -> honest not-found.
        # (Vector search already surfaces the closest chunks, so the pool minimum
        # is the closest semantic match.)
        if not cands or min(c["distance"] for c in cands) > config.RAG_MAX_DISTANCE:
            return {"answer": _NOT_FOUND, "sources": [], "used_chunks": 0}

        # Opt-in LLM re-rank for sharper ordering, else MMR for diversity. Pass the
        # contextualized retrieval_text (not the bare question) so a follow-up like
        # "what's the dosage?" isn't reranked out of context.
        if config.RAG_RERANK:
            hits = _llm_rerank(retrieval_text, cands, top_k)
        else:
            hits = vectorstore._mmr_select(q_vec, cands, top_k, config.RAG_MMR_LAMBDA)

        # Trim weak chunks out of the grounding context: keep only hits within the
        # relevance threshold (but always keep at least the best one), so a single
        # far chunk picked for MMR diversity doesn't add noise to the answer.
        within = [h for h in hits if h.get("distance", 1.0) <= config.RAG_MAX_DISTANCE]
        hits = within or hits[:1]
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
180 words.

Report only what is EXPLICITLY written — do not interpret. Never turn a raw value,
symptom, or abbreviation into a named diagnosis, and never label an unlabelled
number (e.g. "204/100") as a measurement like blood pressure unless the document
says so. Text marked "[?]" was unclear on the scan: mark it as uncertain rather
than stating it as fact."""


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
