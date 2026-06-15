"""Section-aware text chunking for embedding.

Medical documents are organized in sections (Medications, Lab Results,
Assessment, Advice, …). We split on those section boundaries first so a chunk
stays within one section, then size-split only sections that are too large —
prepending the section header to each piece so context isn't lost. Falls back
to a plain recursive split when no clear sections are detected.
"""

from __future__ import annotations

import config
from langchain_text_splitters import RecursiveCharacterTextSplitter

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=config.CHUNK_SIZE,
    chunk_overlap=config.CHUNK_OVERLAP,
    separators=["\n\n", "\n", ". ", " ", ""],
)

# Words that commonly begin a section heading in clinical documents.
_HEADER_KEYWORDS = (
    "medication", "medicine", "prescription", "rx", "diagnos", "assessment",
    "impression", "lab", "result", "finding", "investigation", "advice",
    "history", "complaint", "plan", "vitals", "allerg", "summary", "report",
)


def _is_header(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 60:
        return False
    low = s.lower().rstrip(":")
    # "Medications:" / "Assessment:" — short, colon-terminated.
    if s.endswith(":") and len(s.split()) <= 6:
        return True
    # ALL-CAPS short heading.
    if s.isupper() and len(s.split()) <= 6:
        return True
    # Begins with a known section keyword.
    return any(low == k or low.startswith(k + " ") or low.startswith(k + ":")
               for k in _HEADER_KEYWORDS)


def _sections(text: str) -> list[tuple[str, str]]:
    """Split text into (header, body) sections."""
    sections: list[tuple[str, list[str]]] = []
    header, body = "", []
    for line in text.splitlines():
        if _is_header(line):
            if header or body:
                sections.append((header, body))
            header, body = line.strip(), []
        else:
            body.append(line)
    if header or body:
        sections.append((header, body))
    return [(h, "\n".join(b).strip()) for h, b in sections]


def chunk_text(text: str) -> list[str]:
    """Split text into section-aware, overlapping chunks."""
    text = (text or "").strip()
    if not text:
        return []

    chunks: list[str] = []
    for header, body in _sections(text):
        block = f"{header}\n{body}".strip() if header else body
        if not block:
            continue
        if len(block) <= config.CHUNK_SIZE:
            chunks.append(block)
        else:
            # Section too big: size-split the body, re-attaching the header.
            for sub in _splitter.split_text(body):
                sub = sub.strip()
                if not sub:
                    continue
                chunks.append(f"{header}\n{sub}".strip() if header else sub)

    chunks = [c for c in chunks if c.strip()]
    # Fallback: nothing sensible -> plain recursive split.
    if not chunks:
        chunks = [c.strip() for c in _splitter.split_text(text) if c.strip()]
    return chunks
