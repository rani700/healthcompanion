"""Tests for the activity/retention policy."""

from __future__ import annotations

import sqlite3


def _setup(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CHROMA_DIR", tmp_path / "chroma")
    monkeypatch.setattr(config, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "p.db")
    return config


def _backdate(db_path, patient_ids, when="2021-01-01T00:00:00+00:00"):
    con = sqlite3.connect(db_path)
    con.executemany(
        "UPDATE patients SET last_activity_at = ? WHERE id = ?",
        [(when, pid) for pid in patient_ids],
    )
    con.commit()
    con.close()


def test_active_since_filter(tmp_path, monkeypatch):
    config = _setup(tmp_path, monkeypatch)
    from healthcompanion import patients

    pid = patients.create_patient("P")          # active now
    patients.link_doctor_patient("doc1", pid)

    assert patients.list_patients_for_doctor("doc1")  # no filter -> visible
    # A future "active_since" makes everything look inactive -> filtered out.
    assert patients.list_patients_for_doctor("doc1", active_since="2099-01-01T00:00:00+00:00") == []
    # A past threshold keeps the active patient.
    assert patients.list_patients_for_doctor("doc1", active_since="2000-01-01T00:00:00+00:00")
    _ = config


def test_purge_on_fresh_db_without_users_table(tmp_path, monkeypatch):
    """Purge must not crash on a fresh DB where no signup has created the users
    table yet (the owned-patient check joins it)."""
    config = _setup(tmp_path, monkeypatch)
    from healthcompanion import patients, retention, vectorstore

    monkeypatch.setattr(vectorstore, "delete_collection", lambda pid: None)

    orphan = patients.create_patient("Orphan")  # created via patients only; no users table yet
    _backdate(config.DB_PATH, [orphan])
    purged = retention.purge_inactive()  # must not raise "no such table: users"
    assert orphan in purged


def test_purge_inactive_keeps_self_accounts(tmp_path, monkeypatch):
    config = _setup(tmp_path, monkeypatch)
    from healthcompanion import auth, patients, retention, vectorstore

    # Avoid touching a real Chroma store.
    monkeypatch.setattr(vectorstore, "delete_collection", lambda pid: None)

    orphan = patients.create_patient("Orphan")          # doctor-created, no account
    active = patients.create_patient("Active")          # doctor-created, recent
    self_user = auth.signup("self@x.com", "secret123", "Self", "patient", dob="1990-01-01")
    self_pid = self_user["patient_id"]                  # self-registered

    # Backdate the orphan and the self-account patient to long ago.
    _backdate(config.DB_PATH, [orphan, self_pid])

    purged = retention.purge_inactive()

    assert orphan in purged                              # inactive + no account -> purged
    assert self_pid not in purged                        # self-account -> kept
    assert active not in purged                          # recent -> kept
    assert patients.get_patient(orphan) is None
    assert patients.get_patient(self_pid) is not None    # account + history preserved
    assert patients.get_patient(active) is not None
