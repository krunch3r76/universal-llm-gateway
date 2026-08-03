"""Seat claim store — exclusive claim primitive for agent seats.

Default TTL is 300s (override globally via ``CORTEX_SEAT_CLAIM_TTL_S`` or per
claim via ``ttl_s``). Recommended heartbeat cadence: TTL/3.

``end_reason`` values stored here are mechanical bare strings: ``released``,
``stale``, ``superseded``. Judgment-class terminals (e.g. an operator revoking
a claim) must carry ``by=`` / ``ref=`` inline — do not add a bare ``revoked``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from agent_seat.registry import normalize_bus_address

DEFAULT_TTL_S = float(os.environ.get("CORTEX_SEAT_CLAIM_TTL_S", "300.0"))


def _now_iso() -> str:
    return (
        datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _parse_iso(ts: str) -> float:
    normalized = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).timestamp()


def _expires_in_s(row: sqlite3.Row, now_ts: float) -> float:
    ref = row["last_heartbeat_at"] or row["claimed_at"]
    if ref is None:
        return 0.0
    elapsed = now_ts - _parse_iso(ref)
    return max(0.0, float(row["ttl_s"]) - elapsed)


def _is_stale(row: sqlite3.Row, now_ts: float) -> bool:
    ref = row["last_heartbeat_at"] or row["claimed_at"]
    if ref is None:
        return True
    elapsed = now_ts - _parse_iso(ref)
    return elapsed >= float(row["ttl_s"])


def _reclaim_stale_held(
    conn: sqlite3.Connection, *, now_iso: str, now_ts: float
) -> None:
    rows = conn.execute(
        "SELECT * FROM seat_claims WHERE status = 'held'"
    ).fetchall()
    for row in rows:
        if _is_stale(row, now_ts):
            conn.execute(
                "UPDATE seat_claims SET status='reclaimed', ended_at=?, "
                "end_reason='stale' WHERE id=? AND status='held'",
                (now_iso, row["id"]),
            )


def _holder_projection(row: sqlite3.Row, now_ts: float) -> dict[str, Any]:
    claimed_ts = _parse_iso(row["claimed_at"])
    return {
        "seat": row["seat"],
        "holder_id": row["holder_id"],
        "claimed_at": row["claimed_at"],
        "last_heartbeat_at": row["last_heartbeat_at"],
        "age_s": now_ts - claimed_ts,
        "expires_in_s": _expires_in_s(row, now_ts),
    }


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    raw_meta = data.pop("metadata", None)
    data["metadata"] = json.loads(raw_meta) if raw_meta else None
    return data


def claim_seat(
    conn: sqlite3.Connection,
    *,
    claim_key: str,
    seat: str,
    ttl_s: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attempt exclusive claim inside one BEGIN IMMEDIATE transaction."""
    canonical_seat = normalize_bus_address(seat)
    effective_ttl = DEFAULT_TTL_S if ttl_s is None else float(ttl_s)
    now_iso = _now_iso()
    now_ts = datetime.now(UTC).timestamp()

    conn.execute("BEGIN IMMEDIATE")
    try:
        _reclaim_stale_held(conn, now_iso=now_iso, now_ts=now_ts)

        existing = conn.execute(
            "SELECT * FROM seat_claims WHERE claim_key=? AND status='held'",
            (claim_key,),
        ).fetchone()

        if existing is not None:
            if existing["seat"] == canonical_seat:
                conn.commit()
                return {
                    "granted": True,
                    "holder_id": existing["holder_id"],
                    "claim_key": claim_key,
                    "seat": canonical_seat,
                    "claimed_at": existing["claimed_at"],
                    "ttl_s": float(existing["ttl_s"]),
                }
            conn.commit()
            return {
                "granted": False,
                "holder": _holder_projection(existing, now_ts),
            }

        holder_id = uuid.uuid4().hex
        metadata_json = json.dumps(metadata) if metadata is not None else None
        conn.execute(
            "INSERT INTO seat_claims "
            "(claim_key, seat, holder_id, status, claimed_at, last_heartbeat_at, "
            "ttl_s, metadata) VALUES (?, ?, ?, 'held', ?, NULL, ?, ?)",
            (
                claim_key,
                canonical_seat,
                holder_id,
                now_iso,
                effective_ttl,
                metadata_json,
            ),
        )
        conn.commit()
        return {
            "granted": True,
            "holder_id": holder_id,
            "claim_key": claim_key,
            "seat": canonical_seat,
            "claimed_at": now_iso,
            "ttl_s": effective_ttl,
        }
    except Exception:
        conn.rollback()
        raise


def heartbeat_seat(conn: sqlite3.Connection, *, holder_id: str) -> dict[str, Any]:
    """Refresh liveness for an held claim."""
    now_iso = _now_iso()
    now_ts = datetime.now(UTC).timestamp()

    conn.execute("BEGIN IMMEDIATE")
    try:
        _reclaim_stale_held(conn, now_iso=now_iso, now_ts=now_ts)

        row = conn.execute(
            "SELECT * FROM seat_claims WHERE holder_id=? AND status='held'",
            (holder_id,),
        ).fetchone()
        if row is None:
            conn.commit()
            return {"ok": False, "last_heartbeat_at": None, "expires_in_s": 0.0}

        conn.execute(
            "UPDATE seat_claims SET last_heartbeat_at=? "
            "WHERE holder_id=? AND status='held'",
            (now_iso, holder_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM seat_claims WHERE holder_id=?",
            (holder_id,),
        ).fetchone()
        assert row is not None
        return {
            "ok": True,
            "last_heartbeat_at": now_iso,
            "expires_in_s": _expires_in_s(row, now_ts),
        }
    except Exception:
        conn.rollback()
        raise


def release_seat(conn: sqlite3.Connection, *, holder_id: str) -> dict[str, Any]:
    """Cleanly release an held claim."""
    now_iso = _now_iso()
    now_ts = datetime.now(UTC).timestamp()

    conn.execute("BEGIN IMMEDIATE")
    try:
        _reclaim_stale_held(conn, now_iso=now_iso, now_ts=now_ts)

        row = conn.execute(
            "SELECT 1 FROM seat_claims WHERE holder_id=? AND status='held'",
            (holder_id,),
        ).fetchone()
        if row is None:
            conn.commit()
            return {"released": False, "end_reason": None}

        conn.execute(
            "UPDATE seat_claims SET status='released', ended_at=?, end_reason='released' "
            "WHERE holder_id=? AND status='held'",
            (now_iso, holder_id),
        )
        conn.commit()
        return {"released": True, "end_reason": "released"}
    except Exception:
        conn.rollback()
        raise


def list_seat_claims(
    conn: sqlite3.Connection,
    *,
    claim_key: str | None = None,
    seat: str | None = None,
    include_ended: bool = False,
) -> dict[str, Any]:
    """List claims after lazy stale reclamation."""
    now_iso = _now_iso()
    now_ts = datetime.now(UTC).timestamp()

    conn.execute("BEGIN IMMEDIATE")
    try:
        _reclaim_stale_held(conn, now_iso=now_iso, now_ts=now_ts)

        clauses: list[str] = []
        params: list[Any] = []
        if not include_ended:
            clauses.append("status = 'held'")
        if claim_key is not None:
            clauses.append("claim_key = ?")
            params.append(claim_key)
        if seat is not None:
            clauses.append("seat = ?")
            params.append(normalize_bus_address(seat))

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM seat_claims{where} ORDER BY id",
            tuple(params),
        ).fetchall()
        conn.commit()
        return {"claims": [_row_to_dict(row) for row in rows]}
    except Exception:
        conn.rollback()
        raise
