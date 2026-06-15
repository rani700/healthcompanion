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

# --- Environment -------------------------------------------------------------
# "production" enables strict startup checks (see assert_secure_for_production()).
ENV = os.getenv("HC_ENV", "dev").lower()

# --- Auth --------------------------------------------------------------------
# Secret used to sign JWT session tokens. MUST be overridden in production.
_DEV_SECRET = "dev-insecure-change-me"
JWT_SECRET = os.getenv("HC_JWT_SECRET", _DEV_SECRET)
IS_DEV_SECRET = JWT_SECRET == _DEV_SECRET
JWT_ALGORITHM = "HS256"
TOKEN_TTL_HOURS = int(os.getenv("HC_TOKEN_TTL_HOURS", "12"))

# --- CORS --------------------------------------------------------------------
# Comma-separated allowed origins; defaults to the local Vite dev server.
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "HC_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if o.strip()
]

# --- Guardrail ---------------------------------------------------------------
# When strict, an unparseable classifier response REJECTS the upload (fail closed)
# instead of allowing it through (fail open).
GUARDRAIL_STRICT = os.getenv("HC_GUARDRAIL_STRICT", "false").lower() in ("1", "true", "yes")

# --- Login throttle ----------------------------------------------------------
LOGIN_MAX_ATTEMPTS = int(os.getenv("HC_LOGIN_MAX_ATTEMPTS", "8"))
LOGIN_WINDOW_SECONDS = int(os.getenv("HC_LOGIN_WINDOW_SECONDS", "300"))

# --- Retention ---------------------------------------------------------------
# Patients with no activity for this many days drop out of doctors' views;
# those with no self-registered account are then purged. Self-registered
# patients always keep their account and history.
RETENTION_DAYS = int(os.getenv("HC_RETENTION_DAYS", "730"))  # ~2 years

# --- Document deletion -------------------------------------------------------
# A patient may delete their OWN upload only within this window (accidental
# upload). Doctors can never delete documents.
DOC_DELETE_WINDOW_SECONDS = int(os.getenv("HC_DOC_DELETE_WINDOW_SECONDS", "3600"))

# --- Retrieval / chunking ----------------------------------------------------
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
TOP_K = 5

# Over-fetch this many candidates, then MMR-select TOP_K for diversity so an
# answer draws from multiple documents/visits rather than near-duplicate chunks.
RAG_CANDIDATES = int(os.getenv("HC_RAG_CANDIDATES", "20"))
RAG_MMR_LAMBDA = float(os.getenv("HC_RAG_MMR_LAMBDA", "0.6"))  # relevance vs diversity
# Backstop: if even the closest chunk is farther than this cosine distance, treat
# as "not in records" (calibrated ~0.85; the grounded prompt is the primary guard).
RAG_MAX_DISTANCE = float(os.getenv("HC_RAG_MAX_DISTANCE", "0.85"))
# Max prior conversation turns to include for follow-up questions.
RAG_HISTORY_TURNS = int(os.getenv("HC_RAG_HISTORY_TURNS", "6"))
# Opt-in LLM re-ranking of candidates (sharper ordering, +1 model call/question).
RAG_RERANK = os.getenv("HC_RAG_RERANK", "false").lower() in ("1", "true", "yes")

# Inline-bytes ceiling for the Gemini request; above this we use the Files API.
INLINE_MAX_BYTES = 20 * 1024 * 1024  # 20 MB

# Hard cap on a single uploaded file.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB


def ensure_dirs() -> None:
    """Create the runtime data directories if they don't exist."""
    for d in (DATA_DIR, CHROMA_DIR, UPLOADS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def assert_secure_for_production() -> None:
    """Refuse to run in production with the insecure default JWT secret.

    Called at app startup. In dev/test (ENV != "production") this is a no-op, so
    local runs and the offline test suite don't need a secret configured.
    """
    if ENV == "production" and IS_DEV_SECRET:
        raise RuntimeError(
            "HC_JWT_SECRET is unset (using the insecure dev default) but HC_ENV=production. "
            "Refusing to start — set a strong HC_JWT_SECRET."
        )
