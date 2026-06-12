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
        lambda pid: {"summary": "stub summary", "has_records": True},
    )
    return api.app


def _doctor(app):
    c = TestClient(app)
    r = c.post("/auth/signup", json={"email": "d@x.com", "password": "secret123",
                                     "name": "Dr X", "role": "doctor"})
    return c, r.json()["token"]


def H(t):
    return {"Authorization": f"Bearer {t}"}


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
    pid = c.post("/patients", json={"name": "A"}, headers=H(tok)).json()["id"]
    r = c.patch(f"/patients/{pid}", json={"phone": "999", "sex": "F"}, headers=H(tok))
    assert r.status_code == 200
    assert r.json()["phone"] == "999" and r.json()["sex"] == "F"


def test_scope_mine_vs_all(client):
    c, tok = _doctor(client)
    # Doctor creates one (auto-linked)
    mine_id = c.post("/patients", json={"name": "Mine"}, headers=H(tok)).json()["id"]
    # A self-registered patient the doctor hasn't touched
    c.post("/auth/signup", json={"email": "p@x.com", "password": "secret123",
                                 "name": "Stranger", "role": "patient"})

    all_ids = {p["id"] for p in c.get("/patients?scope=all", headers=H(tok)).json()}
    mine_ids = {p["id"] for p in c.get("/patients?scope=mine", headers=H(tok)).json()}

    assert len(all_ids) >= 2
    assert mine_ids == {mine_id}  # only the one the doctor created


def test_opening_summary_links_patient_to_doctor(client):
    c, tok = _doctor(client)
    # Patient registers themselves; doctor not yet linked.
    c.post("/auth/signup", json={"email": "self@x.com", "password": "secret123",
                                 "name": "Self Reg", "role": "patient"})
    other = [p for p in c.get("/patients?scope=all", headers=H(tok)).json()][0]

    assert other["id"] not in {p["id"] for p in c.get("/patients?scope=mine", headers=H(tok)).json()}
    # Opening the summary should link them.
    assert c.get(f"/patients/{other['id']}/summary", headers=H(tok)).status_code == 200
    assert other["id"] in {p["id"] for p in c.get("/patients?scope=mine", headers=H(tok)).json()}
