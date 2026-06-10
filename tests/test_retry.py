"""Tests for the transient-error retry wrapper around Gemini calls."""

from __future__ import annotations

import pytest

from healthcompanion import gemini_client as gc


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(gc.time, "sleep", lambda *_: None)


class FakeServerError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def test_retries_then_succeeds():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise FakeServerError(503, "503 UNAVAILABLE high demand")
        return "ok"

    assert gc.call_with_retry(flaky, attempts=5) == "ok"
    assert calls["n"] == 3


def test_gives_up_after_attempts():
    def always_503():
        raise FakeServerError(503, "503 UNAVAILABLE")

    with pytest.raises(FakeServerError):
        gc.call_with_retry(always_503, attempts=3)


def test_non_retryable_raises_immediately():
    calls = {"n": 0}

    def bad_request():
        calls["n"] += 1
        raise FakeServerError(400, "400 INVALID_ARGUMENT")

    with pytest.raises(FakeServerError):
        gc.call_with_retry(bad_request, attempts=5)
    assert calls["n"] == 1  # no retries on a 4xx


def test_retryable_by_message_without_code():
    calls = {"n": 0}

    def overloaded():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("The model is overloaded, please try again")
        return "recovered"

    assert gc.call_with_retry(overloaded, attempts=3) == "recovered"
