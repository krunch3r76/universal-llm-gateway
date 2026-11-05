"""Checkpoint fold with fold_version and sha256 integrity."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from .fault_injection import maybe_during_checkpoint_write
from .journal import FOLD_VERSION
from .views import snapshot_state


def _state_digest(state: dict[str, Any]) -> str:
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write_checkpoint(
    conn: sqlite3.Connection,
    *,
    concern_id: str,
    fold_version: int = FOLD_VERSION,
) -> dict[str, Any]:
    """Persist a checkpoint at the current contiguous through_seq."""
    state = snapshot_state(conn, concern_id)
    if state["through_seq"] <= 0:
        raise ValueError("cannot checkpoint before any committed transition")
    digest = _state_digest(state)
    folded = json.dumps(state, sort_keys=True)
    maybe_during_checkpoint_write(state)
    conn.execute(
        "INSERT INTO concern_checkpoint "
        "(concern_id, through_seq, fold_version, folded_state, state_sha256) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(concern_id, through_seq) DO UPDATE SET "
        "fold_version=excluded.fold_version, folded_state=excluded.folded_state, "
        "state_sha256=excluded.state_sha256",
        (concern_id, state["through_seq"], fold_version, folded, digest),
    )
    conn.commit()
    return {
        "concern_id": concern_id,
        "through_seq": state["through_seq"],
        "fold_version": fold_version,
        "state_sha256": digest,
    }


def latest_valid_checkpoint(
    conn: sqlite3.Connection, concern_id: str, *, expected_fold_version: int = FOLD_VERSION
) -> dict[str, Any] | None:
    """Return newest checkpoint whose sha matches folded_state and fold_version."""
    rows = conn.execute(
        "SELECT through_seq, fold_version, folded_state, state_sha256 "
        "FROM concern_checkpoint WHERE concern_id = ? ORDER BY through_seq DESC",
        (concern_id,),
    ).fetchall()
    for through_seq, fold_version, folded_state, state_sha256 in rows:
        if int(fold_version) != expected_fold_version:
            continue
        try:
            state = json.loads(folded_state)
        except json.JSONDecodeError:
            continue
        if _state_digest(state) != state_sha256:
            continue
        return {
            "through_seq": int(through_seq),
            "fold_version": int(fold_version),
            "state": state,
            "state_sha256": state_sha256,
        }
    return None
