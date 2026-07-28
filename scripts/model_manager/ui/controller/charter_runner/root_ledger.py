"""Typed RootLedger — sqlite load/store/seed and derived cortex mirror."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

ConveyorPhase = Literal["dormant", "active"]

from libs.charter_runner_store.db import (
    default_ledger_path,
    execute_with_retry,
    open_ledger_db,
)

Attendance = str  # attended | autonomous


class RootStatus(StrEnum):
    IDLE = "IDLE"
    ADMITTED = "ADMITTED"
    HARVEST_WAIT = "HARVEST_WAIT"
    CONSULT_QUEUED = "CONSULT_QUEUED"
    CONSULT_ADMITTED = "CONSULT_ADMITTED"
    CONSULT_DEFERRED = "CONSULT_DEFERRED"
    WORKER_FAILED = "WORKER_FAILED"
    BLOCKED = "BLOCKED"
    CLOSED = "CLOSED"


class Transition(StrEnum):
    ADMIT_WORKER = "ADMIT_WORKER"
    QUEUE_CONSULT = "QUEUE_CONSULT"
    ADMIT_CONSULT = "ADMIT_CONSULT"
    DEFER_CONSULT = "DEFER_CONSULT"
    ADVANCE_PICKUP = "ADVANCE_PICKUP"
    HARVEST_OK = "HARVEST_OK"
    HARVEST_REVISE = "HARVEST_REVISE"
    WORKER_FAILED = "WORKER_FAILED"
    BLOCK = "BLOCK"
    NOOP = "NOOP"


@dataclass(frozen=True)
class RootLedgerRow:
    root_id: str
    status: RootStatus
    pickup_gid: str | None
    pickup_lane: str | None
    pickup_executor: str | None
    attendance: Attendance
    scoreboard_uri: str
    wip_window_id: str | None = None
    revise_count: int = 0
    consult_role: str | None = None
    consult_attempts: int = 0
    consult_next_retry: float | None = None
    consult_poll_from: str | None = None
    harvest_deadline: float | None = None
    last_window_id: str | None = None
    last_transition: str | None = None
    last_error: str | None = None
    env_facts_json: str | None = None
    conveyor_phase: ConveyorPhase = "dormant"
    pickup_append_cursor: int = 0
    updated_at: float = 0.0


@dataclass(frozen=True)
class SeedConfirm:
    """Human-confirmed seed tuple (spec §F.1 gate)."""

    root_id: str
    pickup_gid: str
    pickup_lane: str
    attendance: Attendance = "attended"
    pickup_executor: str | None = None
    scoreboard_uri: str = ""


def load_root(conn, root_id: str) -> RootLedgerRow | None:
    row = conn.execute(
        "SELECT * FROM root_ledger WHERE root_id = ?", (root_id,)
    ).fetchone()
    if row is None:
        return None
    return _row_from_sqlite(row)


def load_all_roots(conn) -> list[RootLedgerRow]:
    rows = conn.execute("SELECT * FROM root_ledger ORDER BY root_id").fetchall()
    return [_row_from_sqlite(r) for r in rows]


def upsert_root(conn, row: RootLedgerRow) -> None:
    now = time.time()
    execute_with_retry(
        conn,
        """
        INSERT INTO root_ledger (
          root_id, schema_version, status, pickup_gid, pickup_lane, pickup_executor,
          wip_window_id, revise_count, consult_role, consult_attempts,
          consult_next_retry, consult_poll_from, harvest_deadline, attendance,
          scoreboard_uri, last_window_id, last_transition, last_error,
          env_facts_json, conveyor_phase, pickup_append_cursor, updated_at
        ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(root_id) DO UPDATE SET
          status=excluded.status, pickup_gid=excluded.pickup_gid,
          pickup_lane=excluded.pickup_lane, pickup_executor=excluded.pickup_executor,
          wip_window_id=excluded.wip_window_id, revise_count=excluded.revise_count,
          consult_role=excluded.consult_role, consult_attempts=excluded.consult_attempts,
          consult_next_retry=excluded.consult_next_retry,
          consult_poll_from=excluded.consult_poll_from,
          harvest_deadline=excluded.harvest_deadline, attendance=excluded.attendance,
          scoreboard_uri=excluded.scoreboard_uri, last_window_id=excluded.last_window_id,
          last_transition=excluded.last_transition, last_error=excluded.last_error,
          env_facts_json=excluded.env_facts_json,
          conveyor_phase=excluded.conveyor_phase,
          pickup_append_cursor=excluded.pickup_append_cursor,
          updated_at=excluded.updated_at
        """,
        (
            row.root_id,
            row.status.value,
            row.pickup_gid,
            row.pickup_lane,
            row.pickup_executor,
            row.wip_window_id,
            row.revise_count,
            row.consult_role,
            row.consult_attempts,
            row.consult_next_retry,
            row.consult_poll_from,
            row.harvest_deadline,
            row.attendance,
            row.scoreboard_uri,
            row.last_window_id,
            row.last_transition,
            row.last_error,
            row.env_facts_json,
            row.conveyor_phase,
            row.pickup_append_cursor,
            now,
        ),
    )


def seed_from_confirm(conn, confirm: SeedConfirm) -> RootLedgerRow:
    """Write human-confirmed seed; never guess on mismatch (caller validates)."""
    row = RootLedgerRow(
        root_id=confirm.root_id,
        status=RootStatus.IDLE,
        pickup_gid=confirm.pickup_gid,
        pickup_lane=confirm.pickup_lane,
        pickup_executor=confirm.pickup_executor,
        attendance=confirm.attendance,
        scoreboard_uri=confirm.scoreboard_uri
        or f"cortex://notes/system/threads/{confirm.root_id}-charter-scoreboard.md",
    )
    upsert_root(conn, row)
    write_cortex_mirror(row)
    return row


def write_cortex_mirror(row: RootLedgerRow) -> str:
    """Derived write-only mirror — kernel never reads this back."""
    uri = f"cortex://notes/system/threads/charter-ledger/{row.root_id}.json"
    payload = {
        "root_id": row.root_id,
        "status": row.status.value,
        "pickup": {
            "gid": row.pickup_gid,
            "lane": row.pickup_lane,
            "executor": row.pickup_executor,
        },
        "attendance": row.attendance,
        "scoreboard_uri": row.scoreboard_uri,
        "last_transition": row.last_transition,
        "last_error": row.last_error,
        "conveyor_phase": row.conveyor_phase,
        "pickup_append_cursor": row.pickup_append_cursor,
        "updated_at": row.updated_at,
    }
    try:
        from cortex_store.dispatch_ops.ops_entities import _op_entity_write_json

        _op_entity_write_json(
            entity_id=f"charter-ledger/{row.root_id}",
            content=json.dumps(payload, indent=2, sort_keys=True),
            entity_type="artifact",
        )
    except Exception:  # noqa: BLE001 — mirror is best-effort derived
        _write_mirror_via_fs(uri, payload)
    return uri


def _write_mirror_via_fs(uri: str, payload: dict[str, Any]) -> None:
    path = uri.removeprefix("cortex://")
    target = Path.home() / ".local" / "share" / "cortex" / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _row_from_sqlite(row) -> RootLedgerRow:
    return RootLedgerRow(
        root_id=row["root_id"],
        status=RootStatus(row["status"]),
        pickup_gid=row["pickup_gid"],
        pickup_lane=row["pickup_lane"],
        pickup_executor=row["pickup_executor"],
        wip_window_id=row["wip_window_id"],
        revise_count=int(row["revise_count"] or 0),
        consult_role=row["consult_role"],
        consult_attempts=int(row["consult_attempts"] or 0),
        consult_next_retry=row["consult_next_retry"],
        consult_poll_from=row["consult_poll_from"],
        harvest_deadline=row["harvest_deadline"],
        attendance=row["attendance"],
        scoreboard_uri=row["scoreboard_uri"],
        last_window_id=row["last_window_id"],
        last_transition=row["last_transition"],
        last_error=row["last_error"],
        env_facts_json=row["env_facts_json"],
        conveyor_phase=row["conveyor_phase"] or "dormant",
        pickup_append_cursor=int(row["pickup_append_cursor"] or 0),
        updated_at=float(row["updated_at"] or 0),
    )


def open_default_ledger():
    return open_ledger_db(default_ledger_path())


__all__ = [
    "ConveyorPhase",
    "RootLedgerRow",
    "RootStatus",
    "SeedConfirm",
    "Transition",
    "load_all_roots",
    "load_root",
    "open_default_ledger",
    "seed_from_confirm",
    "upsert_root",
    "write_cortex_mirror",
]
