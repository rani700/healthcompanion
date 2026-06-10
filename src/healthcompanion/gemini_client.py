"""Shared Gemini client.

A single lazily-created `genai.Client` is reused across the app. We avoid creating
the client at import time so that modules can be imported (and tested with mocks)
without a real API key present.
"""

from __future__ import annotations

import config

_client = None


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
