"""Hop-before-checkpoint precedence: send-path hook + read projection."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from .lifecycle_fold import (
    empty_hop_fold_state,
    fold_hop_precedence,
)

_BODY_FIELD_RE = re.compile(
    r"^(?P<key>required_checkpoint|read_sha256|uri|cp_ordinal|target_seat|work_key)"
    r"\s*:\s*(?P<value>.+)$",
    re.IGNORECASE,
)


def is_checkpoint_turn_subject(subject: str) -> bool:
    return subject.strip().upper().startswith("CHECKPOINT")


def is_hop_turn_subject(subject: str) -> bool:
    upper = subject.strip().upper()
    return upper.startswith("HOP|") or upper.startswith("HOP")


def parse_turn_body_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in body.splitlines():
        match = _BODY_FIELD_RE.match(line.strip())
        if not match:
            continue
        key = match.group("key").lower()
        value = match.group("value").strip()
        if key == "required_checkpoint":
            fields["required_checkpoint_uri"] = value
        else:
            fields[key] = value
    return fields


def classify_turn_as_lifecycle_event(
    *,
    turn_id: int,
    subject: str,
    body: str,
    structured: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Map a posted turn to checkpoint.sealed or hop.requested, if applicable."""
    extra = structured or {}
    body_fields = parse_turn_body_fields(body)

    if is_checkpoint_turn_subject(subject):
        uri = extra.get("uri") or body_fields.get("uri")
        read_sha256 = extra.get("read_sha256") or body_fields.get("read_sha256")
        cp_ordinal = extra.get("cp_ordinal") or body_fields.get("cp_ordinal")
        if cp_ordinal is not None:
            try:
                cp_ordinal = int(cp_ordinal)
            except (TypeError, ValueError):
                cp_ordinal = None
        return {
            "event_type": "checkpoint.sealed",
            "refs": {
                "turn_id": turn_id,
                "uri": uri,
                "read_sha256": read_sha256,
                "cp_ordinal": cp_ordinal,
            },
        }

    if is_hop_turn_subject(subject):
        uri = (
            extra.get("required_checkpoint_uri")
            or body_fields.get("required_checkpoint_uri")
        )
        read_sha256 = extra.get("required_read_sha256") or body_fields.get(
            "read_sha256"
        )
        target_seat = extra.get("target_seat") or body_fields.get("target_seat")
        return {
            "event_type": "hop.requested",
            "refs": {
                "turn_id": turn_id,
                "required_checkpoint_uri": uri,
                "required_read_sha256": read_sha256,
                "target_seat": target_seat,
            },
        }
    return None


def _checkpoints_from_turns(conn: sqlite3.Connection, thread_id: str) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT id, body FROM turns WHERE thread = ? "
        "AND UPPER(TRIM(subject)) LIKE 'CHECKPOINT%'",
        (thread_id,),
    ).fetchall()
    checkpoints: dict[str, Any] = {}
    for row in rows:
        fields = parse_turn_body_fields(str(row["body"] or ""))
        uri = fields.get("uri")
        if not uri:
            continue
        cp_ordinal = fields.get("cp_ordinal")
        if cp_ordinal is not None:
            try:
                cp_ordinal = int(cp_ordinal)
            except ValueError:
                cp_ordinal = None
        checkpoints[uri] = {
            "turn_id": int(row["id"]),
            "read_sha256": fields.get("read_sha256"),
            "cp_ordinal": cp_ordinal,
        }
    return checkpoints


def _load_hop_fold_state(conn: sqlite3.Connection, thread_id: str) -> dict[str, Any]:
    state = empty_hop_fold_state()
    state["checkpoints_by_uri"] = _checkpoints_from_turns(conn, thread_id)
    hops: dict[str, Any] = {}
    rows = conn.execute(
        "SELECT hop_turn_id, required_checkpoint_uri, required_read_sha256, "
        "hop_status, held_reason, checkpoint_turn_id, "
        "checkpoint_read_sha256 "
        "FROM hop_precedence_state WHERE thread_id = ?",
        (thread_id,),
    ).fetchall()
    for row in rows:
        hop_turn_id = int(row["hop_turn_id"])
        hops[str(hop_turn_id)] = {
            "hop_turn_id": hop_turn_id,
            "required_checkpoint_uri": row["required_checkpoint_uri"],
            "required_read_sha256": row["required_read_sha256"],
            "hop_status": row["hop_status"],
            "held_reason": row["held_reason"],
            "checkpoint_turn_id": row["checkpoint_turn_id"],
            "checkpoint_read_sha256": row["checkpoint_read_sha256"],
        }
    state["hops"] = hops
    return state


def _upsert_hop_row(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    work_key: str | None,
    hop: dict[str, Any],
    updated_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO hop_precedence_state (
            thread_id, work_key, hop_turn_id, required_checkpoint_uri,
            required_read_sha256, hop_status, held_reason,
            checkpoint_turn_id, checkpoint_read_sha256, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(thread_id, hop_turn_id) DO UPDATE SET
            work_key = excluded.work_key,
            required_checkpoint_uri = excluded.required_checkpoint_uri,
            required_read_sha256 = excluded.required_read_sha256,
            hop_status = excluded.hop_status,
            held_reason = excluded.held_reason,
            checkpoint_turn_id = excluded.checkpoint_turn_id,
            checkpoint_read_sha256 = excluded.checkpoint_read_sha256,
            updated_at = excluded.updated_at
        """,
        (
            thread_id,
            work_key,
            int(hop["hop_turn_id"]),
            hop.get("required_checkpoint_uri"),
            hop.get("required_read_sha256"),
            hop["hop_status"],
            hop.get("held_reason"),
            hop.get("checkpoint_turn_id"),
            hop.get("checkpoint_read_sha256"),
            updated_at,
        ),
    )


def maybe_append_lifecycle_event(conn: sqlite3.Connection, event: dict[str, Any]) -> None:
    """Single call site for S1 ``append_lifecycle_event``; no-op until S1 lands."""
    del conn, event


def apply_hop_precedence_on_turn_send(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    turn_id: int,
    subject: str,
    body: str,
    updated_at: str,
    structured: dict[str, Any] | None = None,
) -> None:
    """Classify turn, fold hop precedence, persist projection (same txn as INSERT)."""
    event = classify_turn_as_lifecycle_event(
        turn_id=turn_id,
        subject=subject,
        body=body,
        structured=structured,
    )
    if event is None:
        return
    fields = parse_turn_body_fields(body)
    work_key = (structured or {}).get("work_key") or fields.get("work_key")
    prior = _load_hop_fold_state(conn, thread_id)
    new_state = fold_hop_precedence(prior, event)

    if event["event_type"] == "hop.requested":
        hop_turn_id = str(event["refs"]["turn_id"])
        hop = new_state.get("hops", {}).get(hop_turn_id)
        if hop is not None:
            _upsert_hop_row(
                conn,
                thread_id=thread_id,
                work_key=work_key,
                hop=hop,
                updated_at=updated_at,
            )
    elif event["event_type"] == "checkpoint.sealed":
        uri = event["refs"].get("uri")
        if uri:
            for hop in new_state.get("hops", {}).values():
                if hop.get("required_checkpoint_uri") == uri:
                    _upsert_hop_row(
                        conn,
                        thread_id=thread_id,
                        work_key=work_key,
                        hop=hop,
                        updated_at=updated_at,
                    )

    maybe_append_lifecycle_event(conn, event)


def get_hop_precedence(
    conn: sqlite3.Connection,
    *,
    thread_id: str | None = None,
    work_key: str | None = None,
) -> list[dict[str, Any]]:
    if thread_id is not None:
        rows = conn.execute(
            "SELECT thread_id, work_key, hop_turn_id, required_checkpoint_uri, "
            "required_read_sha256, hop_status, held_reason, checkpoint_turn_id, "
            "checkpoint_read_sha256, updated_at "
            "FROM hop_precedence_state WHERE thread_id = ? "
            "ORDER BY hop_turn_id",
            (thread_id,),
        ).fetchall()
    elif work_key is not None:
        rows = conn.execute(
            "SELECT thread_id, work_key, hop_turn_id, required_checkpoint_uri, "
            "required_read_sha256, hop_status, held_reason, checkpoint_turn_id, "
            "checkpoint_read_sha256, updated_at "
            "FROM hop_precedence_state WHERE work_key = ? "
            "ORDER BY hop_turn_id",
            (work_key,),
        ).fetchall()
    else:
        return []
    return [dict(row) for row in rows]
