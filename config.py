"""Central configuration. Loads .env and exposes constants used across the app.

All values can be overridden via environment variables (see .env.example).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Paths -------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("HC_DATA_DIR", PROJECT_ROOT / "data"))
CHROMA_DIR = DATA_DIR / "chroma_db"
UPLOADS_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "patients.db"

# --- Gemini ------------------------------------------------------------------
# Accept either GEMINI_API_KEY or GOOGLE_API_KEY (the SDK reads the latter too).
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

MODEL_GEN = os.getenv("HC_MODEL_GEN", "gemini-2.5-flash")
MODEL_EMBED = os.getenv("HC_MODEL_EMBED", "gemini-embedding-001")
EMBED_DIM = int(os.getenv("HC_EMBED_DIM", "768"))

# --- Auth --------------------------------------------------------------------
# Secret used to sign JWT session tokens. MUST be overridden in production.
JWT_SECRET = os.getenv("HC_JWT_SECRET", "dev-insecure-change-me")
JWT_ALGORITHM = "HS256"
TOKEN_TTL_HOURS = int(os.getenv("HC_TOKEN_TTL_HOURS", "12"))

# --- Retrieval / chunking ----------------------------------------------------
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
TOP_K = 5

# Inline-bytes ceiling for the Gemini request; above this we use the Files API.
INLINE_MAX_BYTES = 20 * 1024 * 1024  # 20 MB

# Hard cap on a single uploaded file.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB


def ensure_dirs() -> None:
    """Create the runtime data directories if they don't exist."""
    for d in (DATA_DIR, CHROMA_DIR, UPLOADS_DIR):
        d.mkdir(parents=True, exist_ok=True)
