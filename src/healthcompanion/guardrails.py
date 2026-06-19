"""Content guardrails — keep non-medical documents out of patient records.

After a document is transcribed, we ask Gemini to judge whether it is a genuine
medical record. Non-medical uploads (selfies, marksheets, random screenshots) are
rejected before anything is embedded or stored.
"""

from __future__ import annotations

import json
import logging

import config
from healthcompanion.gemini_client import call_with_retry, get_client

_log = logging.getLogger("healthcompanion.guardrails")


class NotMedicalDocument(Exception):
    """Raised when an uploaded document isn't a medical record."""


_CLASSIFY_PROMPT = """You are a gatekeeper for a clinical records system. Decide
whether the content below is a genuine MEDICAL document — for example a
prescription, lab/test report, radiology/imaging study or film (X-ray, CT, MRI,
ultrasound/sonography, ECG/EKG, mammogram), discharge summary,
clinical/consultation note, vaccination record, or medical bill.

A radiology/imaging study counts as MEDICAL (type "imaging") even when it contains
little text, as long as the content identifies it as a clinical image (it names a
modality/body region, or carries a hospital/patient/exam identifier). Do not reject
an imaging study merely for being short on text.

Things that are NOT medical: academic marksheets/certificates, IDs, invoices for
non-medical goods, selfies or random non-clinical photos, screenshots of chats,
memes, receipts.

Respond with ONLY compact JSON, no prose:
{"medical": true|false, "type": "rx|lab|imaging|note|other|non_medical", "reason": "<=12 words"}"""


def classify_document(text: str) -> dict:
    """Classify whether transcribed content is a medical document."""
    client = get_client()
    from google.genai import types

    snippet = (text or "")[:6000]
    # Build by concatenation, NOT str.format — the prompt contains literal JSON
    # braces that .format() would misread as substitution fields.
    prompt = f'{_CLASSIFY_PROMPT}\n\nDocument content:\n"""\n{snippet}\n"""'
    resp = call_with_retry(
        lambda: client.models.generate_content(
            model=config.MODEL_GEN,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
            ),
        )
    )
    raw = (resp.text or "").strip()
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError
    except Exception:
        # Classifier returned non-JSON. In strict mode fail CLOSED (reject); by
        # default fail open so a transient hiccup doesn't block a real record.
        _log.warning("guardrail classifier returned unparseable output: %r", raw[:120])
        if config.GUARDRAIL_STRICT:
            return {"medical": False, "type": "non_medical",
                    "reason": "could not verify (classifier error)"}
        return {"medical": True, "type": "other", "reason": "classifier-unparsable"}
    return data


def assert_medical(text: str) -> dict:
    """Raise NotMedicalDocument if the content isn't a medical record."""
    result = classify_document(text)
    if not result.get("medical", False):
        reason = result.get("reason") or "it doesn't appear to be a health record"
        raise NotMedicalDocument(
            "This file doesn't look like a medical document, so it wasn't added "
            f"({reason}). Please upload a prescription, report, or clinical note."
        )
    return result
