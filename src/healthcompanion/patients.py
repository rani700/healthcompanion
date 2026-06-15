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
    n_chunks    INTEGER NOT NULL,
    visit_id    TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_patient ON documents(patient_id);
CREATE TABLE IF NOT EXISTS care_relationships (
    doctor_id   TEXT NOT NULL,
    patient_id  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (doctor_id, patient_id)
);
CREATE TABLE IF NOT EXISTS visits (
    id          TEXT PRIMARY KEY,
    patient_id  TEXT NOT NULL,
    doctor_id   TEXT,
    doctor_name TEXT NOT NULL,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    started_at  TEXT NOT NULL,
    closed_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_visits_patient ON visits(patient_id);
"""

# Columns added after the first release; back-filled on connect.
_PATIENT_EXTRA_COLS = ("dob", "sex", "phone", "address", "summary", "summary_sig")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add any columns missing from an older database."""
    pcols = {r[1] for r in conn.execute("PRAGMA table_info(patients)")}
    for col in _PATIENT_EXTRA_COLS:
        if col not in pcols:
            conn.execute(f"ALTER TABLE patients ADD COLUMN {col} TEXT")
    dcols = {r[1] for r in conn.execute("PRAGMA table_info(documents)")}
    if "visit_id" not in dcols:
        conn.execute("ALTER TABLE documents ADD COLUMN visit_id TEXT")


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
    """A signature of the patient's document set.

    Changes when a document is added, removed, OR re-filed to another visit — so
    the cached summary is correctly invalidated in all of those cases.
    """
    import hashlib

    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, COALESCE(visit_id, ''), ingested_at "
            "FROM documents WHERE patient_id = ? ORDER BY id",
            (patient_id,),
        ).fetchall()
    h = hashlib.sha1()
    for r in rows:
        h.update(f"{r[0]}|{r[1]}|{r[2]};".encode())
    return f"{len(rows)}:{h.hexdigest()[:16]}"


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


# --- visits / episodes of care ----------------------------------------------
def create_visit(
    patient_id: str, title: str, doctor_id: str | None, doctor_name: str
) -> dict[str, Any]:
    """Open a new visit (episode of care) for a patient."""
    vid = uuid.uuid4().hex[:12]
    with _connect() as conn:
        conn.execute(
            "INSERT INTO visits "
            "(id, patient_id, doctor_id, doctor_name, title, status, started_at) "
            "VALUES (?, ?, ?, ?, ?, 'open', ?)",
            (vid, patient_id, doctor_id, doctor_name, title, _now()),
        )
    return get_visit(vid)


def get_visit(visit_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM visits WHERE id = ?", (visit_id,)).fetchone()
    return dict(row) if row else None


def list_visits(patient_id: str) -> list[dict[str, Any]]:
    """Visits for a patient, newest first, each with its document count."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT v.*, "
            "(SELECT COUNT(*) FROM documents d WHERE d.visit_id = v.id) AS n_docs "
            "FROM visits v WHERE v.patient_id = ? "
            "ORDER BY v.started_at DESC",
            (patient_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def set_visit_status(visit_id: str, status: str) -> dict[str, Any] | None:
    closed_at = _now() if status == "closed" else None
    with _connect() as conn:
        conn.execute(
            "UPDATE visits SET status = ?, closed_at = ? WHERE id = ?",
            (status, closed_at, visit_id),
        )
    return get_visit(visit_id)


def list_care_team(patient_id: str) -> list[dict[str, Any]]:
    """Doctors who have treated this patient (derived from visits)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT doctor_id, doctor_name, COUNT(*) AS visits, "
            "MAX(started_at) AS last_seen "
            "FROM visits WHERE patient_id = ? AND doctor_id IS NOT NULL "
            "GROUP BY doctor_id, doctor_name ORDER BY last_seen DESC",
            (patient_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_document(
    patient_id: str,
    filename: str,
    doc_type: str,
    doc_date: str | None,
    n_chunks: int,
    doc_id: str | None = None,
    visit_id: str | None = None,
) -> str:
    """Record an ingested document and return its id.

    Pass ``doc_id`` to use a pre-allocated id (so it matches the vector-store
    chunk ids); otherwise one is generated. ``visit_id`` ties it to a visit.
    """
    doc_id = doc_id or uuid.uuid4().hex[:12]
    with _connect() as conn:
        conn.execute(
            "INSERT INTO documents "
            "(id, patient_id, filename, doc_type, doc_date, ingested_at, n_chunks, visit_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (doc_id, patient_id, filename, doc_type, doc_date, _now(), n_chunks, visit_id),
        )
    return doc_id


def get_document(doc_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
    return dict(row) if row else None


def set_document_visit(doc_id: str, visit_id: str | None) -> dict[str, Any] | None:
    """Re-file a document under a visit (or None for general)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE documents SET visit_id = ? WHERE id = ?", (visit_id, doc_id)
        )
    return get_document(doc_id)


def delete_document(doc_id: str) -> None:
    """Remove a document's catalog row (vector chunks are removed separately)."""
    with _connect() as conn:
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


def list_documents(
    patient_id: str, visit_id: str | None = None
) -> list[dict[str, Any]]:
    with _connect() as conn:
        if visit_id:
            rows = conn.execute(
                "SELECT * FROM documents WHERE patient_id = ? AND visit_id = ? "
                "ORDER BY ingested_at",
                (patient_id, visit_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM documents WHERE patient_id = ? ORDER BY ingested_at",
                (patient_id,),
            ).fetchall()
    return [dict(r) for r in rows]
