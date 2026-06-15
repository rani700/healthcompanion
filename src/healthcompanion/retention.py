"""Retention policy.

Patients with no activity for ``config.RETENTION_DAYS`` drop out of doctors'
views (handled by the activity filter in the list endpoints). Those that also
have **no self-registered account** are purged entirely (catalog rows + vector
collection), since nothing references them. Self-registered patients always keep
their account and history.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import config
from healthcompanion import patients, vectorstore

_log = logging.getLogger("healthcompanion.retention")


def cutoff_iso(days: int | None = None) -> str:
    """ISO timestamp `days` ago; activity older than this counts as inactive."""
    days = config.RETENTION_DAYS if days is None else days
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def active_since() -> str:
    """The 'still active' threshold used to filter doctors' patient lists."""
    return cutoff_iso()


def purge_inactive(days: int | None = None) -> list[str]:
    """Hard-delete inactive, self-account-less patients. Returns purged ids."""
    cut = cutoff_iso(days)
    ids = patients.inactive_unowned_patient_ids(cut)
    for pid in ids:
        vectorstore.delete_collection(pid)   # vectors first
        patients.delete_patient_cascade(pid)  # then catalog rows
    if ids:
        _log.info("retention purged %d inactive unowned patient(s)", len(ids))
    return ids
