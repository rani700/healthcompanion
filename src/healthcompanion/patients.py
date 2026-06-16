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
    visit_id    TEXT,
    storage_path TEXT
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
CREATE TABLE IF NOT EXISTS document_shares (
    doc_id     TEXT NOT NULL,
    doctor_id  TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (doc_id, doctor_id)
);
"""

# Columns added after the first release; back-filled on connect.
_PATIENT_EXTRA_COLS = (
    "dob", "sex", "phone", "address", "summary", "summary_sig", "last_activity_at"
)


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
    if "uploaded_by" not in dcols:
        conn.execute("ALTER TABLE documents ADD COLUMN uploaded_by TEXT")
    if "storage_path" not in dcols:
        conn.execute("ALTER TABLE documents ADD COLUMN storage_path TEXT")
    # Back-fill activity time for pre-existing patients so they aren't treated
    # as instantly inactive.
    conn.execute(
        "UPDATE patients SET last_activity_at = created_at "
        "WHERE last_activity_at IS NULL"
    )


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
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO patients "
            "(id, name, dob, sex, phone, address, created_at, last_activity_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, name, dob, sex, phone, address, now, now),
        )
    return pid


def touch_patient(patient_id: str) -> None:
    """Mark a patient as active now (resets the inactivity clock)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE patients SET last_activity_at = ? WHERE id = ?",
            (_now(), patient_id),
        )


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


def list_patients(active_since: str | None = None) -> list[dict[str, Any]]:
    """List patients; with ``active_since`` (ISO time) exclude inactive ones."""
    with _connect() as conn:
        if active_since:
            rows = conn.execute(
                "SELECT * FROM patients "
                "WHERE COALESCE(last_activity_at, created_at) >= ? "
                "ORDER BY created_at",
                (active_since,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM patients ORDER BY created_at").fetchall()
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


def has_care_relationship(doctor_id: str, patient_id: str) -> bool:
    """True if this doctor is in a care relationship with this patient."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM care_relationships WHERE doctor_id = ? AND patient_id = ?",
            (doctor_id, patient_id),
        ).fetchone()
    return row is not None


def list_patients_for_doctor(
    doctor_id: str, active_since: str | None = None
) -> list[dict[str, Any]]:
    """Patients this doctor is dealing with (excluding inactive when filtered)."""
    sql = (
        "SELECT p.* FROM patients p "
        "JOIN care_relationships c ON c.patient_id = p.id "
        "WHERE c.doctor_id = ? "
    )
    params: list[Any] = [doctor_id]
    if active_since:
        sql += "AND COALESCE(p.last_activity_at, p.created_at) >= ? "
        params.append(active_since)
    sql += "ORDER BY p.created_at"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def inactive_unowned_patient_ids(cutoff: str) -> list[str]:
    """Patients inactive since ``cutoff`` AND with no self-registered account.

    These are safe to purge — no one references them. Self-registered patients
    (linked to a user) are never returned here.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id FROM patients "
            "WHERE COALESCE(last_activity_at, created_at) < ? "
            "AND id NOT IN (SELECT patient_id FROM users WHERE patient_id IS NOT NULL)",
            (cutoff,),
        ).fetchall()
    return [r["id"] for r in rows]


def delete_patient_cascade(patient_id: str) -> None:
    """Delete a patient and all their catalog rows (documents, visits, links)."""
    with _connect() as conn:
        conn.execute("DELETE FROM documents WHERE patient_id = ?", (patient_id,))
        conn.execute("DELETE FROM visits WHERE patient_id = ?", (patient_id,))
        conn.execute("DELETE FROM care_relationships WHERE patient_id = ?", (patient_id,))
        conn.execute("DELETE FROM patients WHERE id = ?", (patient_id,))


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


def list_visits(
    patient_id: str, doctor_id: str | None = None
) -> list[dict[str, Any]]:
    """Visits for a patient, newest first, each with its document count.

    With ``doctor_id``, return ONLY visits attended by that doctor (privacy:
    a doctor never sees another doctor's or the patient's self-recorded visits).
    """
    sql = (
        "SELECT v.*, "
        "(SELECT COUNT(*) FROM documents d WHERE d.visit_id = v.id) AS n_docs "
        "FROM visits v WHERE v.patient_id = ? "
    )
    params: list[Any] = [patient_id]
    if doctor_id is not None:
        sql += "AND v.doctor_id = ? "
        params.append(doctor_id)
    sql += "ORDER BY v.started_at DESC"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
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
    uploaded_by: str | None = None,
    storage_path: str | None = None,
) -> str:
    """Record an ingested document and return its id.

    Pass ``doc_id`` to use a pre-allocated id (so it matches the vector-store
    chunk ids); otherwise one is generated. ``visit_id`` ties it to a visit;
    ``uploaded_by`` is the user id who uploaded it (for deletion rules);
    ``storage_path`` is the on-disk path of the original file (for viewing it).
    """
    doc_id = doc_id or uuid.uuid4().hex[:12]
    with _connect() as conn:
        conn.execute(
            "INSERT INTO documents "
            "(id, patient_id, filename, doc_type, doc_date, ingested_at, n_chunks, "
            "visit_id, uploaded_by, storage_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (doc_id, patient_id, filename, doc_type, doc_date, _now(), n_chunks,
             visit_id, uploaded_by, storage_path),
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
    patient_id: str, visit_id: str | None = None, doctor_id: str | None = None
) -> list[dict[str, Any]]:
    """Documents for a patient. With ``doctor_id``, restrict to ones the doctor
    may see: in one of the doctor's visits, uploaded by the doctor, or shared by
    the patient with that doctor."""
    sql = "SELECT * FROM documents WHERE patient_id = ? "
    params: list[Any] = [patient_id]
    if visit_id:
        sql += "AND visit_id = ? "
        params.append(visit_id)
    if doctor_id is not None:
        sql += (
            "AND (uploaded_by = ? "
            "OR visit_id IN (SELECT id FROM visits WHERE patient_id = ? AND doctor_id = ?) "
            "OR id IN (SELECT doc_id FROM document_shares WHERE doctor_id = ?)) "
        )
        params += [doctor_id, patient_id, doctor_id, doctor_id]
    sql += "ORDER BY ingested_at"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def visible_doc_ids_for_doctor(patient_id: str, doctor_id: str) -> list[str]:
    """Document ids a doctor may see for a patient (for summary/ask scoping)."""
    return [d["id"] for d in list_documents(patient_id, doctor_id=doctor_id)]


# --- document sharing (patient -> doctor) -----------------------------------
def share_document(doc_id: str, doctor_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO document_shares (doc_id, doctor_id, created_at) "
            "VALUES (?, ?, ?)",
            (doc_id, doctor_id, _now()),
        )


def unshare_document(doc_id: str, doctor_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "DELETE FROM document_shares WHERE doc_id = ? AND doctor_id = ?",
            (doc_id, doctor_id),
        )


def list_doc_shares(doc_id: str) -> list[str]:
    """Doctor ids a document is shared with."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT doctor_id FROM document_shares WHERE doc_id = ?", (doc_id,)
        ).fetchall()
    return [r["doctor_id"] for r in rows]
