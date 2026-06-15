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
    # H1-compatible multi-doctor flow: a self-registered patient brings in each
    # doctor by requesting them (which establishes the care relationship).
    da = _doctor(client, "a@x.com", "Dr A")
    _doctor(client, "b@x.com", "Dr B")
    r = client.post("/auth/signup", json={"email": "pv@x.com", "password": "secret123",
                                          "name": "PV", "role": "patient", "dob": "1972-03-04"})
    ptok = r.json()["token"]
    pid = r.json()["user"]["patient_id"]
    docs = {d["name"]: d["id"] for d in client.get("/doctors", headers=H(ptok)).json()}

    # Patient requests Dr A, then later Dr B (different issue).
    v1 = client.post(f"/patients/{pid}/visits",
                     json={"title": "Fever and cough", "doctor_id": docs["Dr A"]},
                     headers=H(ptok)).json()
    assert v1["doctor_name"] == "Dr A" and v1["status"] == "open"
    v2 = client.post(f"/patients/{pid}/visits",
                     json={"title": "Knee pain", "doctor_id": docs["Dr B"]},
                     headers=H(ptok)).json()
    assert v2["doctor_name"] == "Dr B"

    # Timeline shows both, newest first.
    visits = client.get(f"/patients/{pid}/visits", headers=H(ptok)).json()
    assert [v["title"] for v in visits] == ["Knee pain", "Fever and cough"]

    # Dr A is now in the patient's care and can close their visit.
    closed = client.post(f"/visits/{v1['id']}/close", headers=H(da)).json()
    assert closed["status"] == "closed" and closed["closed_at"]

    # Care team = both doctors.
    team = client.get(f"/patients/{pid}/care-team", headers=H(ptok)).json()
    assert {d["doctor_name"] for d in team} == {"Dr A", "Dr B"}


def test_doctor_profile_signup_and_edit(client):
    # Signup with a profile
    r = client.post("/auth/signup", json={
        "email": "doc@x.com", "password": "secret123", "name": "Dr Meera",
        "role": "doctor", "specialty": "Cardiology", "clinic": "City Heart",
    })
    user = r.json()["user"]
    assert user["specialty"] == "Cardiology" and user["clinic"] == "City Heart"
    tok = r.json()["token"]

    # Directory exposes profile
    pat = client.post("/auth/signup", json={"email": "p@x.com", "password": "secret123",
                                            "name": "P", "role": "patient", "dob": "1990-01-01"})
    listed = client.get("/doctors", headers=H(pat.json()["token"])).json()[0]
    assert listed["specialty"] == "Cardiology"

    # Doctor edits their profile
    upd = client.patch("/auth/profile", json={"specialty": "Neurology", "clinic": "Brain Clinic"},
                       headers=H(tok)).json()
    assert upd["specialty"] == "Neurology" and upd["clinic"] == "Brain Clinic"


def test_patient_cannot_edit_profile(client):
    r = client.post("/auth/signup", json={"email": "p@x.com", "password": "secret123",
                                          "name": "P", "role": "patient", "dob": "1990-01-01"})
    resp = client.patch("/auth/profile", json={"specialty": "x"}, headers=H(r.json()["token"]))
    assert resp.status_code == 403


def test_doctor_directory(client):
    _doctor(client, "a@x.com", "Dr A")
    _doctor(client, "b@x.com", "Dr B")
    # A patient can see the directory.
    r = client.post("/auth/signup", json={"email": "p@x.com", "password": "secret123",
                                          "name": "P", "role": "patient", "dob": "1990-01-01"})
    tok = r.json()["token"]
    docs = client.get("/doctors", headers=H(tok)).json()
    assert {d["name"] for d in docs} == {"Dr A", "Dr B"}


def test_patient_requests_specific_doctor(client):
    _doctor(client, "a@x.com", "Dr A")
    r = client.post("/auth/signup", json={"email": "p@x.com", "password": "secret123",
                                          "name": "P", "role": "patient", "dob": "1990-01-01"})
    ptok = r.json()["token"]
    pid = r.json()["user"]["patient_id"]
    doc_id = client.get("/doctors", headers=H(ptok)).json()[0]["id"]

    v = client.post(f"/patients/{pid}/visits",
                    json={"title": "Checkup", "doctor_id": doc_id},
                    headers=H(ptok)).json()
    assert v["doctor_name"] == "Dr A" and v["doctor_id"] == doc_id
    team = client.get(f"/patients/{pid}/care-team", headers=H(ptok)).json()
    assert team[0]["doctor_name"] == "Dr A"


def test_move_document_between_visits(client):
    from healthcompanion import patients

    da = _doctor(client, "a@x.com", "Dr A")
    pid = client.post("/patients", json={"name": "M", "dob": "1980-01-01"},
                      headers=H(da)).json()["id"]
    v1 = client.post(f"/patients/{pid}/visits", json={"title": "V1"}, headers=H(da)).json()["id"]
    v2 = client.post(f"/patients/{pid}/visits", json={"title": "V2"}, headers=H(da)).json()["id"]

    # A document filed under V1 (no Gemini needed — registered directly).
    doc_id = patients.add_document(pid, "f.png", "rx", "2026-01-01", 1, visit_id=v1)

    # Move it to V2.
    r = client.patch(f"/documents/{doc_id}", json={"visit_id": v2}, headers=H(da))
    assert r.status_code == 200 and r.json()["visit_id"] == v2
    counts = {v["title"]: v["n_docs"]
              for v in client.get(f"/patients/{pid}/visits", headers=H(da)).json()}
    assert counts["V2"] == 1 and counts["V1"] == 0

    # Move it back to general (no visit).
    client.patch(f"/documents/{doc_id}", json={"visit_id": None}, headers=H(da))
    assert patients.get_document(doc_id)["visit_id"] is None


def test_patient_self_recorded_visit(client):
    # A patient records their own visit -> attributed to "Self-recorded"
    r = client.post("/auth/signup", json={"email": "self@x.com", "password": "secret123",
                                          "name": "Self", "role": "patient", "dob": "1990-01-01"})
    tok = r.json()["token"]
    pid = r.json()["user"]["patient_id"]
    v = client.post(f"/patients/{pid}/visits", json={"title": "Headache"}, headers=H(tok)).json()
    assert v["doctor_id"] is None and v["doctor_name"] == "Self-recorded"
