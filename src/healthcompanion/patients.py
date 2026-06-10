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
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def create_patient(name: str) -> str:
    """Create a patient and return its generated id."""
    pid = uuid.uuid4().hex[:12]
    with _connect() as conn:
        conn.execute(
            "INSERT INTO patients (id, name, created_at) VALUES (?, ?, ?)",
            (pid, name, _now()),
        )
    return pid


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
