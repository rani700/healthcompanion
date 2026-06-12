"""Auth + access-control tests against the FastAPI app (no Gemini calls).

Covers signup, login, token validation, and the doctor-vs-patient access rules.
"""

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

    return TestClient(api.app)


def _signup(client, email, role, name="Test User", pw="secret123"):
    r = client.post(
        "/auth/signup",
        json={"email": email, "password": pw, "name": name, "role": role,
              "dob": "1990-01-01"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_patient_signup_creates_linked_record(client):
    data = _signup(client, "jane@x.com", "patient", name="Jane")
    assert data["token"]
    assert data["user"]["role"] == "patient"
    assert data["user"]["patient_id"]  # patient record was created + linked


def test_duplicate_email_rejected(client):
    _signup(client, "dup@x.com", "patient")
    r = client.post(
        "/auth/signup",
        json={"email": "dup@x.com", "password": "secret123", "name": "x", "role": "patient"},
    )
    assert r.status_code == 400


def test_login_and_me(client):
    _signup(client, "bob@x.com", "doctor", name="Dr Bob")
    bad = client.post("/auth/login", json={"email": "bob@x.com", "password": "wrong"})
    assert bad.status_code == 401

    good = client.post("/auth/login", json={"email": "bob@x.com", "password": "secret123"})
    assert good.status_code == 200
    token = good.json()["token"]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "bob@x.com"


def test_unauthenticated_and_bad_token(client):
    assert client.get("/patients").status_code == 401  # no Authorization header
    bad = client.get("/patients", headers={"Authorization": "Bearer garbage"})
    assert bad.status_code == 401


def test_doctor_sees_all_patient_sees_self(client):
    doc = _signup(client, "doc@x.com", "doctor")
    dtok = doc["token"]
    # Doctor creates an extra bare patient.
    client.post("/patients", json={"name": "Walk-in", "dob": "1980-02-02"},
                headers={"Authorization": f"Bearer {dtok}"})

    pat = _signup(client, "pat@x.com", "patient", name="Pat")
    ptok = pat["token"]

    doc_list = client.get("/patients", headers={"Authorization": f"Bearer {dtok}"}).json()
    pat_list = client.get("/patients", headers={"Authorization": f"Bearer {ptok}"}).json()

    assert len(doc_list) >= 2  # walk-in + the patient
    assert len(pat_list) == 1
    assert pat_list[0]["id"] == pat["user"]["patient_id"]


def test_patient_cannot_access_other_patient(client):
    doc = _signup(client, "doc2@x.com", "doctor")
    dtok = doc["token"]
    other = client.post(
        "/patients", json={"name": "Other", "dob": "1975-06-06"},
        headers={"Authorization": f"Bearer {dtok}"}
    ).json()

    pat = _signup(client, "pat2@x.com", "patient")
    ptok = pat["token"]

    # Patient may read their own docs...
    own = client.get(
        f"/patients/{pat['user']['patient_id']}/documents",
        headers={"Authorization": f"Bearer {ptok}"},
    )
    assert own.status_code == 200

    # ...but not someone else's.
    forbidden = client.get(
        f"/patients/{other['id']}/documents",
        headers={"Authorization": f"Bearer {ptok}"},
    )
    assert forbidden.status_code == 403


def test_patient_cannot_create_patients(client):
    pat = _signup(client, "pat3@x.com", "patient")
    r = client.post(
        "/patients",
        json={"name": "Nope"},
        headers={"Authorization": f"Bearer {pat['token']}"},
    )
    assert r.status_code == 403
