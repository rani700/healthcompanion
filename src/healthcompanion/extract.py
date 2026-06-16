"""Document text extraction via Gemini multimodal vision.

Handles typed PDFs, scanned reports, and handwritten prescriptions uniformly:
the file is handed to Gemini, which transcribes it (its native vision/OCR reads
handwriting and scans). Small files are sent inline; larger ones go through the
Files API.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

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
    "Reading handwriting — be careful and clinically literate:\n"
    "- Medication names are REAL drugs (generic or Indian brand names such as "
    "Mecobalamin/Methylcobalamin, Alfacip/Alfacalcidol, Voveran, Pan, Udiliv, "
    "Defcort, etc.). When a handwritten word is close to a known drug name, read "
    "it as that real drug rather than a meaningless letter-by-letter guess.\n"
    "- Expand standard prescription shorthand: Tab=tablet, Cap=capsule, Syp=syrup, "
    "Inj=injection, OD=once daily, BD=twice daily, TDS=three times daily, "
    "HS=at bedtime, PC/AC=after/before food, x10 days=for 10 days, "
    "1-0-1=morning-none-night, TSF=teaspoonful.\n"
    "- For ANY token you cannot read with confidence, transcribe your best reading "
    "followed by '[?]' so the reader knows it is uncertain — do NOT silently "
    "invent a confident-looking name.\n\n"
    "If it is a prescription, list each medication on its own line as: "
    "name — dosage — frequency/timing — duration (use '(not specified)' for any "
    "part that genuinely isn't written).\n"
    "List any tests/investigations advised, and any diagnosis written.\n"
    "If a date appears on the document, state it.\n"
    "Do not add commentary or information that is not present. "
    "If the document is unreadable, say so."
)


def _guess_mime(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _MIME_BY_EXT:
        return _MIME_BY_EXT[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    if guessed:
        return guessed
    raise ValueError(f"Unsupported or unrecognized file type: {path.name}")


def extract_text(path: str | Path) -> str:
    """Extract the full text content of a document file using Gemini.

    Plain-text files are read directly. Everything else (PDF/image) is sent to
    Gemini for transcription — inline if small, via the Files API if large.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)

    mime = _guess_mime(path)

    # Plain text: no model call needed.
    if mime == "text/plain":
        return path.read_text(encoding="utf-8", errors="replace").strip()

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
            )
        )
    finally:
        # Don't leave the temporary upload sitting on Gemini's Files store.
        if uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass

    text = (response.text or "").strip()
    if not text:
        raise RuntimeError(f"Gemini returned no text for {path.name}")
    return text
