"""Document text extraction via Gemini multimodal vision.

Handles typed PDFs, scanned reports, and handwritten prescriptions uniformly:
the file is handed to Gemini, which transcribes it (its native vision/OCR reads
handwriting and scans). Small files are sent inline; larger ones go through the
Files API.
"""

from __future__ import annotations

import json
import mimetypes
import re
from datetime import date as _date
from pathlib import Path
from typing import Any

import config
from healthcompanion.gemini_client import call_with_retry, get_client

# Map common extensions to the MIME types Gemini accepts.
_MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".txt": "text/plain",
}

_EXTRACTION_PROMPT = (
    "You are an expert medical transcriptionist reading a clinical document for a "
    "patient record system. The document may be a typed report, a scan, or a "
    "HANDWRITTEN prescription (often from an Indian clinic/hospital).\n\n"
    "Transcribe ALL text. Preserve numbers EXACTLY as written — dates, dosages, "
    "lab values, units, flow rates.\n\n"
    "Reading handwriting — be careful and HONEST about uncertainty:\n"
    "- Medication NAMES are the highest-risk field and a wrong name is dangerous. "
    "Transcribe the name from the LETTERS actually written. Do NOT 'correct' or "
    "substitute a different real drug just because it looks similar — that can "
    "produce a confident but WRONG name. If a name is not clearly legible, give "
    "your best letter-by-letter reading immediately followed by '[?]'.\n"
    "- Expand standard prescription shorthand: Tab=tablet, Cap=capsule, Syp=syrup, "
    "Inj=injection, OD=once daily, BD=twice daily, TDS=three times daily, "
    "HS=at bedtime, PC/AC=after/before food, x10 days=for 10 days, "
    "1-0-1=morning-none-night, TSF=teaspoonful.\n"
    "- Use '[?]' SPARINGLY — it is for genuinely illegible tokens only. If you can "
    "read a word with reasonable confidence, transcribe it plainly WITHOUT '[?]'. "
    "Do not hedge clearly-legible handwriting. Reserve '[?]' for the few tokens "
    "(often a drug name or dose) you truly cannot make out; for those, give your "
    "best reading followed by '[?]'. Never present a genuinely uncertain reading "
    "as definite, and never invent text that isn't on the page.\n\n"
    "If it is a prescription, list each medication on its own line as: "
    "name — dosage — frequency/timing — duration (use '(not specified)' for any "
    "part that genuinely isn't written).\n"
    "List any tests/investigations advised, and any diagnosis written.\n"
    "Do not add commentary or information that is not present. "
    "If the document is unreadable, say so.\n\n"
    "Return a JSON object with exactly two fields:\n"
    '  "text": the full transcription following the rules above, and\n'
    '  "document_date": the document\'s own date (visit/report/prescription date, '
    "NOT a date of birth or a future follow-up date) as YYYY-MM-DD. Dates on "
    "Indian documents are day/month/year. If no clear date is written, use an "
    "empty string."
)


def _normalize_date(value: Any) -> str | None:
    """Coerce a model-supplied date into YYYY-MM-DD, or None if absent/implausible."""
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if not m:
        # Accept a DD/MM/YYYY or DD-MM-YYYY fallback (Indian convention).
        m2 = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", s)
        if not m2:
            return None
        d, mo, y = (int(x) for x in m2.groups())
    else:
        y, mo, d = (int(x) for x in m.groups())
    try:
        parsed = _date(y, mo, d)
    except ValueError:
        return None
    # Reject implausible dates (future, or absurdly old) — likely a misread.
    if parsed > _date.today() or y < 1900:
        return None
    return parsed.isoformat()


def _guess_mime(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _MIME_BY_EXT:
        return _MIME_BY_EXT[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    if guessed:
        return guessed
    raise ValueError(f"Unsupported or unrecognized file type: {path.name}")


def extract(path: str | Path) -> dict[str, Any]:
    """Extract a document's full text AND its own date.

    Returns ``{"text": <transcription>, "date": "YYYY-MM-DD" | None}``. Plain-text
    files are read directly (no date detection). Everything else (PDF/image) is
    transcribed by Gemini in JSON mode so the date written on the page can be
    captured — inline if small, via the Files API if large.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)

    mime = _guess_mime(path)

    # Plain text: no model call needed (and no reliable date to detect).
    if mime == "text/plain":
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            raise RuntimeError(f"Empty file: {path.name}")
        return {"text": text, "date": None}

    client = get_client()
    from google.genai import types

    size = path.stat().st_size
    uploaded = None
    if size <= config.INLINE_MAX_BYTES:
        part = types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)
        contents = [part, _EXTRACTION_PROMPT]
    else:
        # Large file (e.g. a big PDF): upload via the Files API and reference it.
        uploaded = client.files.upload(file=str(path))
        contents = [_EXTRACTION_PROMPT, uploaded]

    try:
        response = call_with_retry(
            lambda: client.models.generate_content(
                model=config.MODEL_GEN,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", temperature=0
                ),
            )
        )
    finally:
        # Don't leave the temporary upload sitting on Gemini's Files store.
        if uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass

    raw = (response.text or "").strip()
    text, doc_date = raw, None
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            text = (data.get("text") or "").strip()
            doc_date = _normalize_date(data.get("document_date"))
    except (ValueError, TypeError):
        # Model didn't return clean JSON — fall back to the raw text, no date.
        text = raw
    if not text:
        raise RuntimeError(f"Gemini returned no text for {path.name}")
    return {"text": text, "date": doc_date}


def extract_text(path: str | Path) -> str:
    """Back-compat: just the transcription (see :func:`extract` for text + date)."""
    return extract(path)["text"]
