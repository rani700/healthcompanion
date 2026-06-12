"""SQLite catalog of patients and their documents.

Chroma holds the chunk text + vectors; this is the human-facing registry: who the
patients are and what documents have been ingested for each.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    dob         TEXT,
    sex         TEXT,
    phone       TEXT,
    address     TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id          TEXT PRIMARY KEY,
    patient_id  TEXT NOT NULL REFERENCES patients(id),
    filename    TEXT NOT NULL,
    doc_type    TEXT,
    doc_date    TEXT,
    ingested_at TEXT NOT NULL,
    n_chunks    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_patient ON documents(patient_id);
CREATE TABLE IF NOT EXISTS care_relationships (
    doctor_id   TEXT NOT NULL,
    patient_id  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (doctor_id, patient_id)
);
"""

# Columns added after the first release; back-filled on connect.
_PATIENT_EXTRA_COLS = ("dob", "sex", "phone", "address", "summary", "summary_sig")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add any patient columns missing from an older database."""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(patients)")}
    for col in _PATIENT_EXTRA_COLS:
        if col not in existing:
            conn.execute(f"ALTER TABLE patients ADD COLUMN {col} TEXT")


def _connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


_EDITABLE_FIELDS = ("name", "dob", "sex", "phone", "address")


def create_patient(
    name: str,
    dob: str | None = None,
    sex: str | None = None,
    phone: str | None = None,
    address: str | None = None,
) -> str:
    """Create a patient and return its generated id."""
    pid = uuid.uuid4().hex[:12]
    with _connect() as conn:
        conn.execute(
            "INSERT INTO patients (id, name, dob, sex, phone, address, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pid, name, dob, sex, phone, address, _now()),
        )
    return pid


def update_patient(patient_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    """Update editable demographic fields; ignores unknown/None keys."""
    updates = {k: v for k, v in fields.items() if k in _EDITABLE_FIELDS and v is not None}
    if updates:
        sets = ", ".join(f"{k} = ?" for k in updates)
        with _connect() as conn:
            conn.execute(
                f"UPDATE patients SET {sets} WHERE id = ?",
                (*updates.values(), patient_id),
            )
    return get_patient(patient_id)


def get_patient(patient_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM patients WHERE id = ?", (patient_id,)
        ).fetchone()
    return dict(row) if row else None


def list_patients() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM patients ORDER BY created_at"
        ).fetchall()
    return [dict(r) for r in rows]


# --- doctor <-> patient care relationships ----------------------------------
def link_doctor_patient(doctor_id: str, patient_id: str) -> None:
    """Record that a doctor is dealing with a patient (idempotent)."""
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO care_relationships "
            "(doctor_id, patient_id, created_at) VALUES (?, ?, ?)",
            (doctor_id, patient_id, _now()),
        )


def list_patients_for_doctor(doctor_id: str) -> list[dict[str, Any]]:
    """Patients this doctor is dealing with."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT p.* FROM patients p "
            "JOIN care_relationships c ON c.patient_id = p.id "
            "WHERE c.doctor_id = ? ORDER BY p.created_at",
            (doctor_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# --- cached AI summary ------------------------------------------------------
def docs_fingerprint(patient_id: str) -> str:
    """A signature of the patient's document set; changes when docs change."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(MAX(ingested_at), '') AS m "
            "FROM documents WHERE patient_id = ?",
            (patient_id,),
        ).fetchone()
    return f"{row['n']}:{row['m']}"


def get_cached_summary(patient_id: str) -> tuple[str, str] | None:
    """Return (summary, signature) if a cached summary exists."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT summary, summary_sig FROM patients WHERE id = ?", (patient_id,)
        ).fetchone()
    if not row:
        return None
    return (row["summary"] or "", row["summary_sig"] or "")


def set_cached_summary(patient_id: str, summary: str, sig: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE patients SET summary = ?, summary_sig = ? WHERE id = ?",
            (summary, sig, patient_id),
        )


def add_document(
    patient_id: str,
    filename: str,
    doc_type: str,
    doc_date: str | None,
    n_chunks: int,
    doc_id: str | None = None,
) -> str:
    """Record an ingested document and return its id.

    Pass ``doc_id`` to use a pre-allocated id (so it matches the vector-store
    chunk ids); otherwise one is generated.
    """
    doc_id = doc_id or uuid.uuid4().hex[:12]
    with _connect() as conn:
        conn.execute(
            "INSERT INTO documents "
            "(id, patient_id, filename, doc_type, doc_date, ingested_at, n_chunks) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (doc_id, patient_id, filename, doc_type, doc_date, _now(), n_chunks),
        )
    return doc_id


def list_documents(patient_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE patient_id = ? ORDER BY ingested_at",
            (patient_id,),
        ).fetchall()
    return [dict(r) for r in rows]
