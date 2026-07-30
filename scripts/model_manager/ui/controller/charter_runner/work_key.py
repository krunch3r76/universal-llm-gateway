"""Stable work identity for identical-work refire gating (spec §1)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

WORK_KEY_VERSION = "v1"
_ABSENT = "∅"


@dataclass(frozen=True)
class WorkKeyRecord:
    """One admitted window row in ``charter_window_work_key``."""

    work_key: str
    root_id: str
    window_id: str
    dispatch_id: str | None
    thread_id: str | None
    admitted_at: float
    disposition: str | None = None


def compute_work_key(
    *,
    root_id: str,
    source_ref: str | None,
    pickup_gid: str | None,
    consult_role: str | None,
    admission_mode: str,
) -> str:
    """Stable identity of *the work*, invariant under attempt and packet shape."""
    parts = [
        WORK_KEY_VERSION,
        root_id,
        source_ref or _ABSENT,
        pickup_gid or _ABSENT,
        consult_role or _ABSENT,
        admission_mode,
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


__all__ = ["WORK_KEY_VERSION", "WorkKeyRecord", "compute_work_key"]
