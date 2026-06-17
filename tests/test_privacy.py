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


def test_health_endpoints(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}


def test_doctor_cannot_move_invisible_document(client):
    """A doctor may re-file only documents they can see, not pull another party's
    document into their own visit to gain visibility."""
    from healthcompanion import patients
    da = _doctor(client, "a@x.com")
    atok, aid = da["token"], da["user"]["id"]
    pat = _patient(client)
    ptok, pid, uid = pat["token"], pat["user"]["patient_id"], pat["user"]["id"]
    # Link Dr A (so they can open the patient) and give A a visit to move into.
    vid = client.post(f"/patients/{pid}/visits", json={"title": "checkup", "doctor_id": aid},
                      headers=H(ptok)).json()["id"]
    # A private patient upload Dr A cannot see.
    doc_id = patients.add_document(pid, "private.png", "rx", None, 1, uploaded_by=uid)

    r = client.patch(f"/documents/{doc_id}", json={"visit_id": vid}, headers=H(atok))
    assert r.status_code == 403
    # The patient (who owns it) still can move it.
    assert client.patch(f"/documents/{doc_id}", json={"visit_id": vid}, headers=H(ptok)).status_code == 200


def test_doctor_drafts_prescription(client, monkeypatch):
    import api
    captured = {}
    monkeypatch.setattr(
        api, "ingest_text",
        lambda pid, text, filename, **k: (
            captured.update(text=text, filename=filename, kw=k)
            or {"doc_id": "x", "n_chunks": 1, "filename": filename,
                "doc_type": "rx", "doc_date": k.get("doc_date")}
        ),
    )
    da = _doctor(client, "a@x.com")
    atok, aid = da["token"], da["user"]["id"]
    pat = _patient(client)
    ptok, pid = pat["token"], pat["user"]["patient_id"]
    client.post(f"/patients/{pid}/visits", json={"title": "checkup", "doctor_id": aid}, headers=H(ptok))

    r = client.post(
        f"/patients/{pid}/prescriptions",
        json={"diagnosis": "Type 2 Diabetes",
              "medications": [{"name": "Metformin", "dosage": "500 mg", "frequency": "twice daily"}]},
        headers=H(atok),
    )
    assert r.status_code == 200
    assert "Metformin" in captured["text"] and "Type 2 Diabetes" in captured["text"]
    assert captured["kw"].get("uploaded_by") == aid
    assert captured["kw"].get("doc_type") == "rx"


def test_prescription_requires_medication(client):
    da = _doctor(client, "a@x.com")
    atok, aid = da["token"], da["user"]["id"]
    pat = _patient(client)
    ptok, pid = pat["token"], pat["user"]["patient_id"]
    client.post(f"/patients/{pid}/visits", json={"title": "c", "doctor_id": aid}, headers=H(ptok))
    r = client.post(f"/patients/{pid}/prescriptions", json={"medications": []}, headers=H(atok))
    assert r.status_code == 400


def test_patient_cannot_prescribe(client):
    pat = _patient(client)
    r = client.post(
        f"/patients/{pat['user']['patient_id']}/prescriptions",
        json={"medications": [{"name": "X"}]},
        headers=H(pat["token"]),
    )
    assert r.status_code == 403


def test_delete_locks_after_sharing(client, monkeypatch):
    """A shared document locks once the share window passes; unsharing can't
    reopen the window (first_shared_at is permanent)."""
    import config
    from healthcompanion import patients
    monkeypatch.setattr(config, "SHARE_DELETE_WINDOW_SECONDS", 0)  # lock on share
    da = _doctor(client, "a@x.com")
    aid = da["user"]["id"]
    pat = _patient(client)
    ptok, pid, uid = pat["token"], pat["user"]["patient_id"], pat["user"]["id"]
    doc_id = patients.add_document(pid, "f.png", "rx", None, 1, uploaded_by=uid)

    # Private (unshared) own upload -> deletable.
    docs = {d["id"]: d for d in client.get(f"/patients/{pid}/documents", headers=H(ptok)).json()}
    assert docs[doc_id]["can_delete"] is True

    # Share -> with a zero window it's immediately locked.
    client.post(f"/documents/{doc_id}/share", json={"doctor_id": aid}, headers=H(ptok))
    docs = {d["id"]: d for d in client.get(f"/patients/{pid}/documents", headers=H(ptok)).json()}
    assert docs[doc_id]["can_delete"] is False
    assert client.delete(f"/documents/{doc_id}", headers=H(ptok)).status_code == 403

    # Unsharing must NOT restore deletability.
    client.delete(f"/documents/{doc_id}/share/{aid}", headers=H(ptok))
    assert client.delete(f"/documents/{doc_id}", headers=H(ptok)).status_code == 403


def test_delete_allowed_within_share_window(client, monkeypatch):
    """Just after sharing (inside the window) a patient can still delete."""
    import config
    from healthcompanion import patients
    monkeypatch.setattr(config, "SHARE_DELETE_WINDOW_SECONDS", 3600)
    da = _doctor(client, "a@x.com")
    pat = _patient(client)
    ptok, pid, uid = pat["token"], pat["user"]["patient_id"], pat["user"]["id"]
    doc_id = patients.add_document(pid, "f.png", "rx", None, 1, uploaded_by=uid)
    client.post(f"/documents/{doc_id}/share", json={"doctor_id": da["user"]["id"]}, headers=H(ptok))
    r = client.delete(f"/documents/{doc_id}", headers=H(ptok))
    assert r.status_code == 200 and r.json()["deleted"] == doc_id


def test_view_original_file(client):
    import config
    from healthcompanion import patients

    pat = _patient(client)
    ptok, pid, uid = pat["token"], pat["user"]["patient_id"], pat["user"]["id"]
    config.ensure_dirs()
    f = config.UPLOADS_DIR / "report.txt"
    f.write_text("UROFLOWMETRY graph data")
    with_file = patients.add_document(
        pid, "report.txt", "lab", None, 1, uploaded_by=uid, storage_path=str(f)
    )
    no_file = patients.add_document(pid, "drafted.txt", "rx", None, 1, uploaded_by=uid)

    # The list flags which documents have an original, never the server path.
    docs = {d["id"]: d for d in client.get(f"/patients/{pid}/documents", headers=H(ptok)).json()}
    assert docs[with_file]["has_file"] is True
    assert docs[no_file]["has_file"] is False
    assert "storage_path" not in docs[with_file]

    # Patient can open their original file.
    r = client.get(f"/documents/{with_file}/file", headers=H(ptok))
    assert r.status_code == 200 and r.content == b"UROFLOWMETRY graph data"

    # In-app authored doc (no file) -> 404.
    assert client.get(f"/documents/{no_file}/file", headers=H(ptok)).status_code == 404

    # An unrelated doctor cannot fetch it.
    other = _doctor(client, "other@x.com")
    assert client.get(f"/documents/{with_file}/file", headers=H(other["token"])).status_code in (403, 404)


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
