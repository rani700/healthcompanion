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
    "You are transcribing a medical document for a patient record system. "
    "Transcribe ALL text in this document verbatim, preserving structure and "
    "numbers exactly (dates, dosages, lab values, units).\n"
    "If it is a prescription, also list each medication as: "
    "name — dosage — frequency/timing — duration.\n"
    "If a date appears on the document, state it.\n"
    "Do not add commentary, diagnoses, or information that is not present. "
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
