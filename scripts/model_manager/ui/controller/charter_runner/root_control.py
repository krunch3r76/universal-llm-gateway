"""Per-root operator hold — durable ledger BLOCKED, bus receipt tip, enrollment strip."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, replace
from typing import Any

from universal_logging import get_logger

from scripts.model_manager import observation_event_charter as charter_events

from . import bus_client
from .admission import ENROLLMENT_TAG
from .kernel import hold as tick_hold
from .root_ledger import (
    RootLedgerRow,
    RootStatus,
    Transition,
    load_root,
    open_default_ledger,
    upsert_root,
    write_cortex_mirror,
)

logger = get_logger(__name__)

_HOLD_PREFIX = "operator_hold:"
_RECEIPT = (
    "This turn is a receipt. The hold lives on the root ledger; "
    "bus prose never arms or clears it."
)


@dataclass(frozen=True, slots=True)
class RootControlResult:
    """Outcome of a per-root block/unblock manage op (ledger + bus side effects)."""

    root_id: str
    status: RootStatus
    was_status: str
    unenrolled: bool
    tip_turn: int | None
    tip_class: str | None
    wip_window_id: str | None
    already: str | None
    live_dispatches: list[dict[str, Any]]


def _with_operator_hold(row: RootLedgerRow, payload: dict[str, Any] | None) -> str:
    facts: dict[str, Any] = {}
    if row.env_facts_json:
        try:
            parsed = json.loads(row.env_facts_json)
            facts = parsed if isinstance(parsed, dict) else {}
        except (TypeError, json.JSONDecodeError):
            facts = {}
    if payload is None:
        facts.pop("operator_hold", None)
    else:
        facts["operator_hold"] = payload
    return json.dumps(facts, sort_keys=True)


def _operator_hold_from_row(row: RootLedgerRow) -> dict[str, Any] | None:
    if not row.env_facts_json:
        return None
    try:
        parsed = json.loads(row.env_facts_json)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    hold = parsed.get("operator_hold")
    return hold if isinstance(hold, dict) else None


def _apply_block(
    conn,
    row: RootLedgerRow,
    *,
    reason: str,
    set_by: str,
    clear_wip: bool,
) -> RootLedgerRow:
    blocked = replace(
        row,
        status=RootStatus.BLOCKED,
        last_transition=Transition.BLOCK.value,
        last_error=f"{_HOLD_PREFIX}{reason}",
        wip_window_id=None if clear_wip else row.wip_window_id,
        consult_role=None,
        consult_next_retry=None,
        consult_poll_from=None,
        consult_attempts=0,
        env_facts_json=_with_operator_hold(
            row,
            {
                "reason": reason,
                "set_by": set_by,
                "set_at": time.time(),
                "prior_status": row.status.value,
            },
        ),
        updated_at=time.time(),
    )
    upsert_root(conn, blocked)
    write_cortex_mirror(blocked)
    return blocked


def _apply_unblock(conn, row: RootLedgerRow) -> RootLedgerRow:
    unblocked = replace(
        row,
        status=RootStatus.IDLE,
        last_error=None,
        last_transition=Transition.NOOP.value,
        env_facts_json=_with_operator_hold(row, None),
        updated_at=time.time(),
    )
    upsert_root(conn, unblocked)
    write_cortex_mirror(unblocked)
    return unblocked


def _turn_number_from_send(resp: dict[str, Any]) -> int | None:
    raw = resp.get("turn_number")
    if raw is not None:
        n = int(raw or 0)
        return n if n else None
    turn = resp.get("turn")
    if isinstance(turn, dict):
        n = int(turn.get("turn_number") or 0)
        return n if n else None
    return None


def _tip_class_for(row: RootLedgerRow, live: list[dict[str, Any]]) -> str:
    if row.wip_window_id or live:
        return "NOTE"
    return "BLOCKED"


async def _live_for_root(root_id: str) -> list[dict[str, Any]]:
    try:
        dispatches = await tick_hold.list_live_charter_dispatches()
    except Exception as exc:  # noqa: BLE001 — degrade to empty for hold receipt
        logger.warning("list_live_charter_dispatches failed root=%s: %s", root_id, exc)
        return []
    return [
        d
        for d in dispatches
        if root_id in str(d.get("subject") or "")
        or root_id in str(d.get("thread_id") or "")
    ]


def _receipt_body(
    *,
    set_by: str,
    reason: str,
    prior_status: str,
    unenrolled: bool,
    wip_window_id: str | None,
    live_count: int,
) -> str:
    return (
        "operator hold — charter-runner\n\n"
        f"- set_by: {set_by}\n"
        f"- reason: {reason}\n"
        "- ledger_status: BLOCKED\n"
        f"- prior_status: {prior_status}\n"
        f"- unenrolled: {unenrolled}\n"
        f"- wip_window_id: {wip_window_id}\n"
        f"- live_dispatches: {live_count}\n\n"
        f"{_RECEIPT}"
    )


def _payload_from_result(result: RootControlResult) -> dict[str, Any]:
    data = asdict(result)
    ledger_status = data.pop("status")
    data["root_status"] = (
        ledger_status.value if isinstance(ledger_status, RootStatus) else ledger_status
    )
    data["status"] = "ok"
    return data


def _idempotent_blocked(row: RootLedgerRow) -> dict[str, Any]:
    return _payload_from_result(
        RootControlResult(
            root_id=row.root_id,
            status=RootStatus.BLOCKED,
            was_status=RootStatus.BLOCKED.value,
            unenrolled=False,
            tip_turn=None,
            tip_class=None,
            wip_window_id=row.wip_window_id,
            already="blocked",
            live_dispatches=[],
        )
    )


def _idempotent_unblocked(row: RootLedgerRow) -> dict[str, Any]:
    return _payload_from_result(
        RootControlResult(
            root_id=row.root_id,
            status=row.status,
            was_status=row.status.value,
            unenrolled=False,
            tip_turn=None,
            tip_class=None,
            wip_window_id=row.wip_window_id,
            already="unblocked",
            live_dispatches=[],
        )
    )


async def block_root(
    root_id: str,
    *,
    reason: str,
    set_by: str = "manage",
    unenroll: bool = True,
    clear_wip: bool = False,
) -> dict[str, Any]:
    """Arm a durable per-root hold — stops NEW admits; live dispatches keep running."""
    conn = open_default_ledger()
    try:
        row = load_root(conn, root_id)
        if row is None:
            raise ValueError(f"unknown root: {root_id}")
        if row.status == RootStatus.BLOCKED:
            return _idempotent_blocked(row)
        prior_status = row.status.value
        blocked = _apply_block(
            conn,
            row,
            reason=reason,
            set_by=set_by,
            clear_wip=clear_wip,
        )
    finally:
        conn.close()

    live = await _live_for_root(root_id)
    tip_class = _tip_class_for(row, live)
    tip_turn: int | None = None
    try:
        resp = await bus_client.post_root_turn(
            root_id,
            subject=f"{tip_class} — operator hold: {reason}",
            body=_receipt_body(
                set_by=set_by,
                reason=reason,
                prior_status=prior_status,
                unenrolled=False,
                wip_window_id=blocked.wip_window_id,
                live_count=len(live),
            ),
        )
        tip_turn = _turn_number_from_send(resp)
    except Exception as exc:  # noqa: BLE001 — hold stands without receipt tip
        logger.warning("post_root_turn failed root=%s: %s", root_id, exc)

    unenrolled = False
    if unenroll:
        try:
            unenrolled = bool(
                (await bus_client.unenroll_root(root_id)).get("unenrolled")
            )
        except Exception as exc:  # noqa: BLE001 — blocked-but-enrolled is safe
            logger.warning("unenroll_root failed root=%s: %s", root_id, exc)

    await charter_events.emit_manage_charter_root_blocked(
        root=root_id,
        reason=reason,
        set_by=set_by,
        prior_status=prior_status,
        unenrolled=unenrolled,
        tip_class=tip_class,
        wip_window_id=blocked.wip_window_id,
    )

    return _payload_from_result(
        RootControlResult(
            root_id=root_id,
            status=RootStatus.BLOCKED,
            was_status=prior_status,
            unenrolled=unenrolled,
            tip_turn=tip_turn,
            tip_class=tip_class,
            wip_window_id=blocked.wip_window_id,
            already=None,
            live_dispatches=live,
        )
    )


async def unblock_root(
    root_id: str,
    *,
    set_by: str = "manage",
    reenroll: bool = False,
) -> dict[str, Any]:
    """Clear a per-root hold — BLOCKED becomes IDLE; re-enroll only when requested."""
    conn = open_default_ledger()
    try:
        row = load_root(conn, root_id)
        if row is None:
            raise ValueError(f"unknown root: {root_id}")
        if row.status != RootStatus.BLOCKED:
            return _idempotent_unblocked(row)
        prior_status = row.status.value
        _apply_unblock(conn, row)
    finally:
        conn.close()

    reenrolled = False
    if reenroll:
        try:
            reenrolled = bool((await bus_client.enroll_root(root_id)).get("enrolled"))
        except Exception as exc:  # noqa: BLE001 — ledger unblock already committed
            logger.warning("enroll_root failed root=%s: %s", root_id, exc)

    await charter_events.emit_manage_charter_root_unblocked(
        root=root_id,
        set_by=set_by,
        prior_status=prior_status,
        reenrolled=reenrolled,
    )

    return _payload_from_result(
        RootControlResult(
            root_id=root_id,
            status=RootStatus.IDLE,
            was_status=prior_status,
            unenrolled=False,
            tip_turn=None,
            tip_class=None,
            wip_window_id=row.wip_window_id,
            already=None,
            live_dispatches=[],
        )
    )


async def root_status(root_id: str) -> dict[str, Any]:
    """Read-only per-root hold snapshot — no ledger writes or events."""
    conn = open_default_ledger()
    try:
        row = load_root(conn, root_id)
    finally:
        conn.close()
    if row is None:
        return {"found": False}

    enrolled = False
    try:
        detail = await bus_client.fetch_thread(root_id)
        tags = list(detail.get("tags") or [])
        if "tags" not in detail and isinstance(detail.get("thread"), dict):
            tags = list((detail.get("thread") or {}).get("tags") or [])
        enrolled = ENROLLMENT_TAG in tags
    except Exception as exc:  # noqa: BLE001 — enrollment is best-effort on read
        logger.debug("fetch_thread failed root=%s: %s", root_id, exc)

    return {
        "found": True,
        "root_id": row.root_id,
        "status": row.status.value,
        "enrolled": enrolled,
        "wip_window_id": row.wip_window_id,
        "last_transition": row.last_transition,
        "last_error": row.last_error,
        "consult_role": row.consult_role,
        "consult_attempts": row.consult_attempts,
        "conveyor_phase": row.conveyor_phase,
        "updated_at": row.updated_at,
        "operator_hold": _operator_hold_from_row(row),
    }


__all__ = [
    "RootControlResult",
    "block_root",
    "root_status",
    "unblock_root",
]
