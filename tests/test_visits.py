"""Tests for visits/episodes, care team, and required DOB."""

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


def H(t):
    return {"Authorization": f"Bearer {t}"}


def _doctor(c, email="dr@x.com", name="Dr A"):
    return c.post("/auth/signup", json={"email": email, "password": "secret123",
                                        "name": name, "role": "doctor"}).json()["token"]


def test_dob_required_on_create(client):
    tok = _doctor(client)
    no_dob = client.post("/patients", json={"name": "NoDob"}, headers=H(tok))
    assert no_dob.status_code == 400
    ok = client.post("/patients", json={"name": "HasDob", "dob": "1980-01-01"}, headers=H(tok))
    assert ok.status_code == 200


def test_patient_signup_requires_dob(client):
    bad = client.post("/auth/signup", json={"email": "p@x.com", "password": "secret123",
                                            "name": "P", "role": "patient"})
    assert bad.status_code == 400


def test_visit_lifecycle_and_attribution(client):
    da = _doctor(client, "a@x.com", "Dr A")
    db = _doctor(client, "b@x.com", "Dr B")
    pid = client.post("/patients", json={"name": "Ravi", "dob": "1972-03-04"},
                      headers=H(da)).json()["id"]

    # Dr A opens a visit
    v1 = client.post(f"/patients/{pid}/visits", json={"title": "Fever and cough"},
                     headers=H(da)).json()
    assert v1["doctor_name"] == "Dr A" and v1["status"] == "open"

    # Later, Dr B opens another visit for a different issue
    v2 = client.post(f"/patients/{pid}/visits", json={"title": "Knee pain"},
                     headers=H(db)).json()
    assert v2["doctor_name"] == "Dr B"

    # Timeline shows both, newest first
    visits = client.get(f"/patients/{pid}/visits", headers=H(da)).json()
    assert [v["title"] for v in visits] == ["Knee pain", "Fever and cough"]

    # Close one
    closed = client.post(f"/visits/{v1['id']}/close", headers=H(da)).json()
    assert closed["status"] == "closed" and closed["closed_at"]

    # Care team = both doctors
    team = client.get(f"/patients/{pid}/care-team", headers=H(da)).json()
    names = {d["doctor_name"] for d in team}
    assert names == {"Dr A", "Dr B"}


def test_patient_self_recorded_visit(client):
    # A patient records their own visit -> attributed to "Self-recorded"
    r = client.post("/auth/signup", json={"email": "self@x.com", "password": "secret123",
                                          "name": "Self", "role": "patient", "dob": "1990-01-01"})
    tok = r.json()["token"]
    pid = r.json()["user"]["patient_id"]
    v = client.post(f"/patients/{pid}/visits", json={"title": "Headache"}, headers=H(tok)).json()
    assert v["doctor_id"] is None and v["doctor_name"] == "Self-recorded"
