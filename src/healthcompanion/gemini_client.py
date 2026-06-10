"""Shared Gemini client.

A single lazily-created `genai.Client` is reused across the app. We avoid creating
the client at import time so that modules can be imported (and tested with mocks)
without a real API key present.
"""

from __future__ import annotations

import time

import config

_client = None

# Gemini occasionally returns transient errors (503 model overload, 429 rate
# limit, 500 internal). These are momentary and almost always succeed on retry.
_RETRYABLE_CODES = (429, 500, 503)
_RETRYABLE_HINTS = ("unavailable", "overloaded", "high demand", "try again",
                    "resource_exhausted", "internal error", "deadline")


def _status_code(exc: Exception):
    for attr in ("code", "status_code"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    return None


def _is_retryable(exc: Exception) -> bool:
    code = _status_code(exc)
    if code in _RETRYABLE_CODES:
        return True
    msg = str(exc).lower()
    return any(h in msg for h in _RETRYABLE_HINTS)


def call_with_retry(fn, *, attempts: int = 5, base_delay: float = 1.0,
                    max_delay: float = 8.0):
    """Call ``fn`` and retry transient Gemini errors with exponential backoff.

    Non-retryable errors (bad key, invalid request) are raised immediately.
    """
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - we re-raise non-retryable ones
            last_exc = exc
            if not _is_retryable(exc) or i == attempts - 1:
                raise
            time.sleep(min(base_delay * (2**i), max_delay))
    assert last_exc is not None
    raise last_exc


def get_client():
    """Return a shared genai.Client, creating it on first use.

    Raises a clear error if no API key is configured.
    """
    global _client
    if _client is None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError(
                "No Gemini API key found. Set GEMINI_API_KEY in your .env file "
                "(see .env.example)."
            )
        # Imported here so the module can be imported without the SDK installed
        # (e.g. during mocked unit tests).
        from google import genai

        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client
