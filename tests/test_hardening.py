"""Tests for the security/robustness hardening pass."""

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
    monkeypatch.setattr(config, "LOGIN_MAX_ATTEMPTS", 3)
    import api

    return TestClient(api.app)


def H(t):
    return {"Authorization": f"Bearer {t}"}


def _doctor(c, email="d@x.com"):
    return c.post("/auth/signup", json={"email": email, "password": "secret123",
                                        "name": "Dr X", "role": "doctor"}).json()["token"]


def test_production_secret_guard(monkeypatch):
    import config
    monkeypatch.setattr(config, "ENV", "production")
    monkeypatch.setattr(config, "IS_DEV_SECRET", True)
    with pytest.raises(RuntimeError):
        config.assert_secure_for_production()
    # Not production -> no raise.
    monkeypatch.setattr(config, "ENV", "dev")
    config.assert_secure_for_production()


def test_login_throttle(client):
    client.post("/auth/signup", json={"email": "t@x.com", "password": "secret123",
                                      "name": "T", "role": "doctor"})
    # Wrong password up to the limit, then throttled.
    for _ in range(3):
        r = client.post("/auth/login", json={"email": "t@x.com", "password": "WRONG"})
        assert r.status_code == 401
    blocked = client.post("/auth/login", json={"email": "t@x.com", "password": "WRONG"})
    assert blocked.status_code == 429


def test_doctor_cannot_delete_document(client):
    """Doctors can never delete a document, even their own patient's."""
    from healthcompanion import patients
    tok = _doctor(client)
    pid = client.post("/patients", json={"name": "D", "dob": "1980-01-01"},
                      headers=H(tok)).json()["id"]
    doc_id = patients.add_document(pid, "f.png", "rx", "2026-01-01", 1)
    r = client.delete(f"/documents/{doc_id}", headers=H(tok))
    assert r.status_code == 403


def test_patient_deletes_own_recent_upload(client):
    """A patient can delete their own upload within the window."""
    from healthcompanion import patients
    su = client.post("/auth/signup", json={"email": "p@x.com", "password": "secret123",
                                           "name": "P", "role": "patient",
                                           "dob": "1990-01-01"}).json()
    ptok = su["token"]
    uid = su["user"]["id"]
    pid = su["user"]["patient_id"]
    # Document uploaded by this patient just now.
    doc_id = patients.add_document(pid, "f.png", "rx", "2026-01-01", 1, uploaded_by=uid)
    r = client.delete(f"/documents/{doc_id}", headers=H(ptok))
    assert r.status_code == 200 and r.json()["deleted"] == doc_id
    assert client.get(f"/patients/{pid}/documents", headers=H(ptok)).json() == []


def test_patient_cannot_delete_someone_elses_document(client):
    """A patient cannot delete a document in another patient's record."""
    from healthcompanion import patients
    dtok = _doctor(client)
    pid = client.post("/patients", json={"name": "Owner", "dob": "1980-01-01"},
                      headers=H(dtok)).json()["id"]
    doc_id = patients.add_document(pid, "f.png", "rx", "2026-01-01", 1)
    ptok = client.post("/auth/signup", json={"email": "p@x.com", "password": "secret123",
                                             "name": "P", "role": "patient",
                                             "dob": "1990-01-01"}).json()["token"]
    assert client.delete(f"/documents/{doc_id}", headers=H(ptok)).status_code == 403


def test_fingerprint_changes_on_visit_move(tmp_path, monkeypatch):
    """Summary fingerprint must change when a document is re-filed to a visit."""
    import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "p.db")
    from healthcompanion import patients
    pid = patients.create_patient("FP")
    doc_id = patients.add_document(pid, "f.png", "rx", "2026-01-01", 1)
    before = patients.docs_fingerprint(pid)
    patients.set_document_visit(doc_id, "visit-123")
    after = patients.docs_fingerprint(pid)
    assert before != after
