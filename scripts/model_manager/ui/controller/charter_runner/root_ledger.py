"""Typed RootLedger — sqlite load/store/admit and derived cortex mirror."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from universal_logging import get_logger

from libs.charter_runner_store.db import (
    default_ledger_path,
    execute_with_retry,
    open_ledger_db,
)

from .admission.typed_work_item import (
    Attendance,
    TypedAdmitError,
    TypedWorkItemAdmit,
    typed_record_valid,
    validate_typed_admit,
)

logger = get_logger(__name__)

ConveyorPhase = Literal["dormant", "active"]


class RootStatus(StrEnum):
    """Lifecycle states for one charter root on the typed ledger."""

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
    """Kernel transition labels persisted on ``last_transition`` for telemetry."""

    ADMIT_WORKER = "ADMIT_WORKER"
    QUEUE_CONSULT = "QUEUE_CONSULT"
    ADMIT_CONSULT = "ADMIT_CONSULT"
    DEFER_CONSULT = "DEFER_CONSULT"
    ADVANCE_PICKUP = "ADVANCE_PICKUP"
    HARVEST_OK = "HARVEST_OK"
    HARVEST_REVISE = "HARVEST_REVISE"
    WORKER_FAILED = "WORKER_FAILED"
    BLOCK = "BLOCK"
    HEAL_CONSULT_QUEUED = "HEAL_CONSULT_QUEUED"
    STATE_CLOSE = "STATE_CLOSE"
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
    """Legacy human-confirmed seed tuple — migrate to ``TypedWorkItemAdmit``."""

    root_id: str
    pickup_gid: str
    pickup_lane: str
    attendance: Attendance = "attended"
    pickup_executor: str | None = None
    scoreboard_uri: str = ""


def _facts_dict(row: RootLedgerRow) -> dict[str, Any]:
    raw = row.env_facts_json
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _with_fact_timestamp(
    row: RootLedgerRow, key: str, *, at: float | None = None
) -> RootLedgerRow:
    facts = _facts_dict(row)
    facts[key] = float(at if at is not None else time.time())
    return RootLedgerRow(
        root_id=row.root_id,
        status=row.status,
        pickup_gid=row.pickup_gid,
        pickup_lane=row.pickup_lane,
        pickup_executor=row.pickup_executor,
        attendance=row.attendance,
        scoreboard_uri=row.scoreboard_uri,
        wip_window_id=row.wip_window_id,
        revise_count=row.revise_count,
        consult_role=row.consult_role,
        consult_attempts=row.consult_attempts,
        consult_next_retry=row.consult_next_retry,
        consult_poll_from=row.consult_poll_from,
        harvest_deadline=row.harvest_deadline,
        last_window_id=row.last_window_id,
        last_transition=row.last_transition,
        last_error=row.last_error,
        env_facts_json=json.dumps(facts, sort_keys=True),
        conveyor_phase=row.conveyor_phase,
        pickup_append_cursor=row.pickup_append_cursor,
        updated_at=time.time(),
    )


def record_wake_at(conn, root_id: str, *, at: float | None = None) -> None:
    """F-BIND(b): stamp ``wake_at`` on the typed work-item row."""
    row = load_root(conn, root_id)
    if row is None:
        return
    upsert_root(conn, _with_fact_timestamp(row, "wake_at", at=at))


def record_harvest_at(conn, root_id: str, *, at: float | None = None) -> None:
    """F-BIND(b): stamp ``harvest_at`` on the typed work-item row."""
    row = load_root(conn, root_id)
    if row is None:
        return
    upsert_root(conn, _with_fact_timestamp(row, "harvest_at", at=at))


def record_advance_at(conn, root_id: str, *, at: float | None = None) -> None:
    """F-BIND(b): stamp ``advance_at`` on the typed work-item row."""
    row = load_root(conn, root_id)
    if row is None:
        return
    upsert_root(conn, _with_fact_timestamp(row, "advance_at", at=at))


def list_open_work_items(
    conn,
    *,
    stale_before: float | None = None,
) -> list[RootLedgerRow]:
    """Open typed work-items for wake-pull floor (ledger query, ¬ tip scan)."""
    rows = load_all_roots(conn)
    open_rows: list[RootLedgerRow] = []
    for row in rows:
        if not typed_record_valid(row):
            continue
        if row.status in (RootStatus.CLOSED, RootStatus.BLOCKED):
            continue
        if stale_before is not None and row.updated_at >= stale_before:
            continue
        open_rows.append(row)
    return open_rows


def list_open_work_item_root_ids(
    conn,
    *,
    stale_before: float | None = None,
) -> set[str]:
    """Root ids for WakeHub mapper and ledger-query floor."""
    return {
        row.root_id
        for row in list_open_work_items(conn, stale_before=stale_before)
        if row.root_id
    }


def admit_work_item(conn, admit: TypedWorkItemAdmit) -> RootLedgerRow:
    """Atomic typed admit — replaces SeedConfirm / PHASE1_SEEDS ceremony."""
    validate_typed_admit(admit)
    scoreboard = (
        str(admit.scoreboard_uri or "").strip()
        or f"cortex://notes/system/threads/{admit.root_id}-charter-scoreboard.md"
    )
    existing = load_root(conn, admit.root_id)
    row = RootLedgerRow(
        root_id=admit.root_id,
        status=existing.status if existing is not None else RootStatus.IDLE,
        pickup_gid=admit.pickup_gid,
        pickup_lane=str(admit.pickup_lane).strip().lower(),
        pickup_executor=admit.pickup_executor,
        attendance=str(admit.attendance).strip().lower(),
        scoreboard_uri=scoreboard,
        wip_window_id=existing.wip_window_id if existing is not None else None,
        revise_count=existing.revise_count if existing is not None else 0,
        consult_role=existing.consult_role if existing is not None else None,
        consult_attempts=existing.consult_attempts if existing is not None else 0,
        consult_next_retry=(
            existing.consult_next_retry if existing is not None else None
        ),
        consult_poll_from=existing.consult_poll_from if existing is not None else None,
        harvest_deadline=existing.harvest_deadline if existing is not None else None,
        last_window_id=existing.last_window_id if existing is not None else None,
        last_transition=existing.last_transition if existing is not None else None,
        last_error=existing.last_error if existing is not None else None,
        env_facts_json=existing.env_facts_json if existing is not None else None,
        conveyor_phase=existing.conveyor_phase if existing is not None else "dormant",
        pickup_append_cursor=(
            existing.pickup_append_cursor if existing is not None else 0
        ),
        updated_at=time.time(),
    )
    upsert_root(conn, row)
    write_cortex_mirror(row)
    return row


def load_root(conn, root_id: str) -> RootLedgerRow | None:
    """Load one root ledger row by id, or None when the root was never admitted."""
    row = conn.execute(
        "SELECT * FROM root_ledger WHERE root_id = ?", (root_id,)
    ).fetchone()
    if row is None:
        return None
    return _row_from_sqlite(row)


def load_all_roots(conn) -> list[RootLedgerRow]:
    """Return every persisted root row ordered by ``root_id`` for floor queries."""
    rows = conn.execute("SELECT * FROM root_ledger ORDER BY root_id").fetchall()
    return [_row_from_sqlite(r) for r in rows]


def upsert_root(conn, row: RootLedgerRow) -> None:
    """Insert or replace one root ledger row and bump ``updated_at`` to now."""
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
    """Legacy seed path — delegates to typed admit for migration."""
    return admit_work_item(
        conn,
        TypedWorkItemAdmit(
            root_id=confirm.root_id,
            pickup_gid=confirm.pickup_gid,
            pickup_lane=confirm.pickup_lane,
            attendance=confirm.attendance,
            pickup_executor=confirm.pickup_executor,
            scoreboard_uri=confirm.scoreboard_uri,
        ),
    )


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
    content = json.dumps(payload, indent=2, sort_keys=True)
    try:
        from cortex_store.dispatch_ops.ops_entities import _op_entity_write_json

        _op_entity_write_json(
            entity_id=f"charter-ledger/{row.root_id}",
            content=content,
            entity_type="artifact",
        )
        return uri
    except Exception:  # noqa: BLE001 — fall through to shared-root / HOME mirror
        pass
    if _write_mirror_to_shared_root(uri, content):
        return uri
    _write_mirror_via_fs(uri, payload)
    logger.warning(
        "charter ledger mirror HOME-only for root_id=%s; not emitting cortex://",
        row.root_id,
    )
    return ""


def _write_mirror_to_shared_root(uri: str, content: str) -> bool:
    from implement_admission.closeout_helpers import cortex_files_root

    rel = uri.removeprefix("cortex://")
    target = cortex_files_root() / rel
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError:
        return False
    return True


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
    """Open the process-default sqlite ledger used by manage-hosted charter runner."""
    return open_ledger_db(default_ledger_path())


__all__ = [
    "ConveyorPhase",
    "RootLedgerRow",
    "RootStatus",
    "SeedConfirm",
    "Transition",
    "TypedAdmitError",
    "TypedWorkItemAdmit",
    "admit_work_item",
    "list_open_work_item_root_ids",
    "list_open_work_items",
    "load_all_roots",
    "load_root",
    "open_default_ledger",
    "record_advance_at",
    "record_harvest_at",
    "record_wake_at",
    "seed_from_confirm",
    "typed_record_valid",
    "upsert_root",
    "validate_typed_admit",
    "write_cortex_mirror",
]
