"""Tests for demographics, care-scoping (mine/all), and the summary endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CHROMA_DIR", tmp_path / "chroma")
    monkeypatch.setattr(config, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "patients.db")

    import api

    # Don't call Gemini for summaries in tests.
    monkeypatch.setattr(
        api, "rag_summarize",
        lambda pid, refresh=False: {"summary": "stub summary", "has_records": True},
    )
    return api.app


def _doctor(app):
    c = TestClient(app)
    r = c.post("/auth/signup", json={"email": "d@x.com", "password": "secret123",
                                     "name": "Dr X", "role": "doctor"})
    return c, r.json()["token"]


def H(t):
    return {"Authorization": f"Bearer {t}"}


def _new(name, **extra):
    """Patient-create payload with the now-required dob."""
    return {"name": name, "dob": "1985-05-05", **extra}


def test_create_with_demographics_and_get(client):
    c, tok = _doctor(client)
    r = c.post("/patients", json={
        "name": "Ravi Kumar", "dob": "1972-03-04", "sex": "M",
        "phone": "080-555-1144", "address": "12 MG Road",
    }, headers=H(tok))
    assert r.status_code == 200
    p = r.json()
    assert p["dob"] == "1972-03-04" and p["phone"] == "080-555-1144"

    got = c.get(f"/patients/{p['id']}", headers=H(tok)).json()
    assert got["address"] == "12 MG Road"


def test_patch_demographics(client):
    c, tok = _doctor(client)
    pid = c.post("/patients", json=_new("A"), headers=H(tok)).json()["id"]
    r = c.patch(f"/patients/{pid}", json={"phone": "999", "sex": "F"}, headers=H(tok))
    assert r.status_code == 200
    assert r.json()["phone"] == "999" and r.json()["sex"] == "F"


def test_doctor_list_is_care_scoped(client):
    # H1: doctor list contains only their own patients, never unrelated ones.
    c, tok = _doctor(client)
    mine_id = c.post("/patients", json=_new("Mine"), headers=H(tok)).json()["id"]
    c.post("/auth/signup", json={"email": "p@x.com", "password": "secret123",
                                 "name": "Stranger", "role": "patient", "dob": "1992-02-02"})
    ids = {p["id"] for p in c.get("/patients", headers=H(tok)).json()}
    assert ids == {mine_id}


def test_summary_caching(monkeypatch, tmp_path):
    """Summary is cached until the document set changes."""
    import config
    from healthcompanion import patients, rag, vectorstore

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "p.db")

    calls = {"n": 0}

    class _Resp:
        text = "GENERATED SUMMARY"

    class _Models:
        def generate_content(self, *a, **k):
            calls["n"] += 1
            return _Resp()

    class _Client:
        models = _Models()

    monkeypatch.setattr(rag, "get_client", lambda: _Client())
    monkeypatch.setattr(
        vectorstore, "get_all_chunks",
        lambda pid, limit=60, visit_id=None: [
            {"text": "x", "doc_type": "rx", "doc_date": "", "filename": "f"}
        ],
    )

    pid = patients.create_patient("Cache Pt")
    # Pretend a document exists so the fingerprint is stable.
    patients.add_document(pid, "f.png", "rx", "2026-01-01", 1)

    r1 = rag.summarize_patient(pid)
    r2 = rag.summarize_patient(pid)  # served from cache
    assert r1["summary"] == "GENERATED SUMMARY"
    assert r2["cached"] is True
    assert calls["n"] == 1  # model called once, not twice

    # A new document changes the fingerprint -> regenerate.
    patients.add_document(pid, "g.png", "lab", "2026-02-01", 1)
    rag.summarize_patient(pid)
    assert calls["n"] == 2


def test_summary_access_is_care_scoped(client):
    # H1: a doctor can summarize their own patient, but not an unrelated one.
    c, tok = _doctor(client)
    pid = c.post("/patients", json=_new("Own"), headers=H(tok)).json()["id"]
    assert c.get(f"/patients/{pid}/summary", headers=H(tok)).status_code == 200

    d2 = c.post("/auth/signup", json={"email": "d2@x.com", "password": "secret123",
                                      "name": "D2", "role": "doctor"}).json()["token"]
    assert c.get(f"/patients/{pid}/summary", headers=H(d2)).status_code == 403
