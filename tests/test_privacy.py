"""Per-doctor privacy scoping: doctors see only their own visits + visible docs;
patients see everything; sharing is patient-controlled."""

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


def _doctor(c, email):
    return c.post("/auth/signup", json={"email": email, "password": "secret123",
                                        "name": email.split("@")[0], "role": "doctor"}).json()


def _patient(c, email="p@x.com"):
    return c.post("/auth/signup", json={"email": email, "password": "secret123",
                                        "name": "Pat", "role": "patient",
                                        "dob": "1990-01-01"}).json()


def test_doctor_sees_only_own_visits(client):
    da = _doctor(client, "a@x.com")
    db = _doctor(client, "b@x.com")
    pat = _patient(client)
    ptok, pid = pat["token"], pat["user"]["patient_id"]
    docs = {d["name"]: d["id"] for d in client.get("/doctors", headers=H(ptok)).json()}

    client.post(f"/patients/{pid}/visits", json={"title": "fever", "doctor_id": docs["a"]}, headers=H(ptok))
    client.post(f"/patients/{pid}/visits", json={"title": "knee", "doctor_id": docs["b"]}, headers=H(ptok))
    client.post(f"/patients/{pid}/visits", json={"title": "self note"}, headers=H(ptok))  # self-recorded

    a_visits = [v["title"] for v in client.get(f"/patients/{pid}/visits", headers=H(da["token"])).json()]
    b_visits = [v["title"] for v in client.get(f"/patients/{pid}/visits", headers=H(db["token"])).json()]
    p_visits = [v["title"] for v in client.get(f"/patients/{pid}/visits", headers=H(ptok)).json()]

    assert a_visits == ["fever"]                 # only Dr A's visit
    assert b_visits == ["knee"]                  # only Dr B's visit
    assert set(p_visits) == {"fever", "knee", "self note"}  # patient sees all


def test_document_visibility_and_sharing(client):
    from healthcompanion import patients
    da = _doctor(client, "a@x.com")
    datok = da["token"]
    a_id = da["user"]["id"]
    pat = _patient(client)
    ptok, pid = pat["token"], pat["user"]["patient_id"]
    uid = pat["user"]["id"]

    # Patient links Dr A (requests a visit) so the doctor can open the patient.
    client.post(f"/patients/{pid}/visits", json={"title": "checkup", "doctor_id": a_id}, headers=H(ptok))
    # Patient uploads a private document (not in any of Dr A's visits).
    doc_id = patients.add_document(pid, "private.png", "rx", None, 1, uploaded_by=uid)

    # Dr A does NOT see the patient's private doc yet.
    a_docs = [d["id"] for d in client.get(f"/patients/{pid}/documents", headers=H(datok)).json()]
    assert doc_id not in a_docs
    assert doc_id in [d["id"] for d in client.get(f"/patients/{pid}/documents", headers=H(ptok)).json()]

    # Patient shares it with Dr A -> now visible.
    assert client.post(f"/documents/{doc_id}/share", json={"doctor_id": a_id}, headers=H(ptok)).status_code == 200
    a_docs = [d["id"] for d in client.get(f"/patients/{pid}/documents", headers=H(datok)).json()]
    assert doc_id in a_docs

    # Doctor cannot manage sharing.
    assert client.post(f"/documents/{doc_id}/share", json={"doctor_id": a_id}, headers=H(datok)).status_code == 403

    # Patient revokes the share -> hidden again.
    client.delete(f"/documents/{doc_id}/share/{a_id}", headers=H(ptok))
    a_docs = [d["id"] for d in client.get(f"/patients/{pid}/documents", headers=H(datok)).json()]
    assert doc_id not in a_docs
