"""Text chunking for embedding.

Uses LangChain's RecursiveCharacterTextSplitter with overlap, so dosages, ranges,
and findings aren't split across boundaries.
"""

from __future__ import annotations

import config
from langchain_text_splitters import RecursiveCharacterTextSplitter

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=config.CHUNK_SIZE,
    chunk_overlap=config.CHUNK_OVERLAP,
    separators=["\n\n", "\n", ". ", " ", ""],
)


def chunk_text(text: str) -> list[str]:
    """Split text into overlapping chunks, dropping empties."""
    return [c.strip() for c in _splitter.split_text(text) if c.strip()]
