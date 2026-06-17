"""User accounts and authentication.

Users live in the same SQLite database as the patient registry. A *patient* user
is linked 1:1 to a patient record (``patient_id``); a *doctor* user has no link and
can access all patients.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

import config
from healthcompanion import patients
from healthcompanion.security import hash_password, verify_password

VALID_ROLES = ("doctor", "patient")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL,
    patient_id    TEXT,
    name          TEXT NOT NULL,
    specialty     TEXT,
    clinic        TEXT,
    created_at    TEXT NOT NULL
);
"""

# Doctor-profile columns added after first release; back-filled on connect.
_USER_EXTRA_COLS = ("specialty", "clinic")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL + wait-timeout so concurrent requests don't trip 'database is locked'.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    existing = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    for col in _USER_EXTRA_COLS:
        if col not in existing:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
    return conn


def ensure_schema() -> None:
    """Ensure the users table exists. Lets cross-module queries (e.g. retention's
    owned-patient check) run safely on a fresh DB before any signup."""
    _connect().close()


class AuthError(Exception):
    """Raised on signup/login problems (duplicate email, bad credentials)."""


def get_user_by_email(email: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.lower(),)
        ).fetchone()
    return dict(row) if row else None


def get_user(user_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def list_doctors() -> list[dict[str, Any]]:
    """Public directory of doctors (id, name, specialty, clinic)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, name, specialty, clinic FROM users "
            "WHERE role = 'doctor' ORDER BY name"
        ).fetchall()
    return [dict(r) for r in rows]


def update_profile(user_id: str, specialty: str | None, clinic: str | None) -> dict[str, Any]:
    """Update a doctor's profile fields, then return the public user."""
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET specialty = ?, clinic = ? WHERE id = ?",
            (specialty, clinic, user_id),
        )
    return _public(get_user(user_id))


def signup(
    email: str,
    password: str,
    name: str,
    role: str,
    dob: str | None = None,
    sex: str | None = None,
    phone: str | None = None,
    address: str | None = None,
    specialty: str | None = None,
    clinic: str | None = None,
) -> dict[str, Any]:
    """Create a user. For ``patient`` role, also create a linked patient record.

    Returns the user dict (without the password hash).
    """
    email = email.strip().lower()
    if not email or not password:
        raise AuthError("Email and password are required.")
    if role not in VALID_ROLES:
        raise AuthError(f"Role must be one of {VALID_ROLES}.")
    if len(password) < 6:
        raise AuthError("Password must be at least 6 characters.")
    if role == "patient" and not dob:
        raise AuthError("Date of birth is required.")
    if get_user_by_email(email):
        raise AuthError("An account with that email already exists.")

    # A patient user gets their own patient record to own.
    patient_id = (
        patients.create_patient(name, dob=dob, sex=sex, phone=phone, address=address)
        if role == "patient"
        else None
    )

    user_id = uuid.uuid4().hex[:12]
    with _connect() as conn:
        conn.execute(
            "INSERT INTO users "
            "(id, email, password_hash, role, patient_id, name, specialty, clinic, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, email, hash_password(password), role, patient_id, name,
             specialty if role == "doctor" else None,
             clinic if role == "doctor" else None, _now()),
        )
    return _public(get_user(user_id))


def login(email: str, password: str) -> dict[str, Any]:
    """Verify credentials and return the public user dict, or raise AuthError."""
    user = get_user_by_email(email.strip().lower())
    if not user or not verify_password(password, user["password_hash"]):
        raise AuthError("Invalid email or password.")
    return _public(user)


def _public(user: dict[str, Any] | None) -> dict[str, Any]:
    """Strip the password hash before returning a user to callers."""
    if user is None:
        raise AuthError("User not found.")
    return {
        "id": user["id"],
        "email": user["email"],
        "role": user["role"],
        "patient_id": user["patient_id"],
        "name": user["name"],
        "specialty": user["specialty"] if "specialty" in user.keys() else None,
        "clinic": user["clinic"] if "clinic" in user.keys() else None,
    }
