"""Operator observability ledger — owed_wake rows and endpoint wake producers.

Producer 1 (milestone): house-authored allowlisted subjects to maestro.
Producer 2 (drain_complete): restart_intent terminal CAS with arming_lanes.

No timer or poller; undispatched rows flush on the next gate read or post.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from universal_logging import get_logger

logger = get_logger(__name__)

MILESTONE_SUBJECT_PREFIXES: tuple[str, ...] = (
    "MILESTONE|",
    "PLAN|",
    "ASK|",
    "FACT|",
    "READY|",
    "DONE|",
    "VERDICT|",
    "LANDED|",
)

_CHROME_SUBJECT_MARKERS: tuple[str, ...] = (
    "CHECKPOINT",
    "LANE CLOSEOUT",
    "cdp generate",
    "cdp reply",
    "cursor-sdk generate",
)

PRODUCER_MILESTONE = "milestone"
PRODUCER_DRAIN_COMPLETE = "drain_complete"


def producer1_cse_dispatch_enabled() -> bool:
    """Producer 1 CSE dispatch — on unless explicitly disabled.

    Unset or blank dispatches (Kaywan K60, agent-bus:18460#80). ``0``,
    ``false``, ``no``, and ``off`` still write the owed_wake row and do
    not call cse_session.
    """
    raw = os.environ.get("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH")
    if raw is None or not raw.strip():
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def subject_qualifies_producer1(subject: str) -> bool:
    """True when subject opens with an allowlisted milestone type prefix."""
    text = (subject or "").strip()
    if not text:
        return False
    return any(text.startswith(prefix) for prefix in MILESTONE_SUBJECT_PREFIXES)


def is_chrome_bus_subject(subject: str) -> bool:
    """Chrome turns must never arm producer 1."""
    text = (subject or "").strip()
    if not text:
        return True
    upper = text.upper()
    if upper.startswith("CHECKPOINT") or upper.startswith("LANE CLOSEOUT"):
        return True
    lowered = text.lower()
    return any(marker in lowered for marker in _CHROME_SUBJECT_MARKERS)


def is_house_authored_turn(*, from_agent: str, user_agent: str | None = None) -> bool:
    """Best-effort house authorship — excludes transport/admit chrome."""
    agent = (from_agent or "").strip().lower()
    if not agent or agent in {
        "frontier-admit",
        "dispatch",
        "cdp",
        "cursor-sdk",
        "mcp",
    }:
        return False
    ua = (user_agent or "").lower()
    if "cursor-sdk" in ua and agent == "cursor":
        return False
    return True


@dataclass(frozen=True, slots=True)
class OwedWakeRow:
    row_id: str
    holder_id: str
    producer: str
    lane_thread_id: str | None
    turn_refs: list[dict[str, Any]]
    arming_lanes: list[str]
    restart_intent_id: str | None
    dispatched_at: str | None
    closed_at: str | None


class BusPoster(Protocol):
    def __call__(
        self, *, thread_id: str, subject: str, body: str
    ) -> bool: ...


class EndpointWakeDriver(Protocol):
    def __call__(
        self,
        *,
        holder_id: str,
        lane_thread_id: str | None,
        wake_kind: str,
        context: dict[str, Any],
    ) -> bool: ...


@dataclass(slots=True)
class OwedWakeDeps:
    post_bus_turn: BusPoster
    endpoint_wake: EndpointWakeDriver
    holder_lookup: Callable[[str], dict[str, Any] | None]
    resolve_superseded_holder: Callable[[str], dict[str, Any] | None]
    lane_uses_bridge_wake: Callable[[str], bool] | None = None


def maybe_producer1_milestone(
    store: Any,
    *,
    subject: str,
    to_agent: str,
    from_agent: str,
    thread_id: str,
    turn_number: int,
    holder_id: str,
    lane_thread_id: str | None,
    user_agent: str | None = None,
    deps: OwedWakeDeps | None = None,
) -> dict[str, Any]:
    """Producer 1 — allowlisted house milestone to maestro."""
    if (to_agent or "").strip().lower() != "maestro":
        return {"recorded": False, "reason": "not_to_maestro"}
    if is_chrome_bus_subject(subject):
        return {"recorded": False, "reason": "chrome_subject"}
    if not is_house_authored_turn(from_agent=from_agent, user_agent=user_agent):
        return {"recorded": False, "reason": "not_house_authored"}
    if not subject_qualifies_producer1(subject):
        return {"recorded": False, "reason": "subject_not_allowlisted"}

    turn_ref = {
        "thread_id": str(thread_id),
        "turn_number": int(turn_number),
        "subject": subject,
    }
    row, created = store.coalesce_owed_wake_milestone(
        holder_id=holder_id,
        lane_thread_id=lane_thread_id,
        turn_ref=turn_ref,
    )
    out: dict[str, Any] = {
        "recorded": True,
        "row_id": row.row_id,
        "created": created,
        "dispatched": False,
    }
    if created and producer1_cse_dispatch_enabled() and deps is not None:
        out["dispatched"] = attempt_dispatch(store, row, deps=deps)
    return out


def producer2_on_restart_terminal(
    store: Any,
    intent: Any,
    *,
    holder_id: str,
    deps: OwedWakeDeps,
) -> dict[str, Any]:
    """Producer 2 — terminal restart_intent CAS arms drain-completion owed_wake."""
    lanes = list(getattr(intent, "arming_lanes", None) or [])
    if not lanes:
        caller_lane = getattr(intent, "caller_thread_id", None)
        if caller_lane:
            lanes = [str(caller_lane)]
    turn_ref = {
        "restart_intent_id": intent.intent_id,
        "service": intent.service,
        "status": intent.status,
    }
    row, created = store.create_owed_wake_drain_complete(
        holder_id=holder_id,
        restart_intent_id=intent.intent_id,
        arming_lanes=lanes,
        lane_thread_id=lanes[0] if lanes else None,
        turn_ref=turn_ref,
    )
    dispatched = attempt_dispatch(store, row, deps=deps) if created else False
    return {
        "row_id": row.row_id,
        "dispatched": dispatched,
        "arming_lanes": lanes,
        "created": created,
    }


def maybe_flush_pending_owed_wakes(*, store: Any | None = None) -> int:
    """Backstop flush for manage gate reads and posts; failures are non-fatal."""
    try:
        from services.git_integration_worker.cse_session_holders import (
            build_owed_wake_deps,
        )

        from .restart_intent_store import RestartIntentStore

        resolved = store if store is not None else RestartIntentStore.instance()
        return flush_pending_owed_wakes(resolved, deps=build_owed_wake_deps())
    except Exception:
        logger.debug("maybe_flush_pending_owed_wakes failed", exc_info=True)
        return 0


def flush_pending_owed_wakes(store: Any, *, deps: OwedWakeDeps) -> int:
    """Backstop: retry dispatch for open rows with dispatched_at null."""
    count = 0
    for row in store.list_open_undispatched_owed_wakes():
        if row.producer == PRODUCER_MILESTONE and not producer1_cse_dispatch_enabled():
            continue
        if attempt_dispatch(store, row, deps=deps):
            count += 1
    return count


def attempt_dispatch(store: Any, row: OwedWakeRow, *, deps: OwedWakeDeps) -> bool:
    """Post bus turns and run endpoint wake; leave dispatched_at null on failure."""
    if row.closed_at is not None or row.dispatched_at is not None:
        return row.dispatched_at is not None

    holder = deps.holder_lookup(row.holder_id)
    wake_plan = _plan_endpoint_wake(
        holder,
        row=row,
        resolve_superseded=deps.resolve_superseded_holder,
        lane_uses_bridge_wake=deps.lane_uses_bridge_wake,
    )
    if wake_plan.get("skip"):
        store.mark_owed_wake_dispatched(row.row_id, outcome="held_no_live_seat")
        return True

    bus_ok = True
    lanes = row.arming_lanes or (
        [row.lane_thread_id] if row.lane_thread_id else []
    )
    if row.producer == PRODUCER_DRAIN_COMPLETE:
        for lane in lanes:
            lane_id = str(lane).strip()
            if not lane_id:
                continue
            subject = f"FACT|restart_intent terminal — owed wake ({row.restart_intent_id})"
            body = json.dumps(
                {"owed_wake_row_id": row.row_id, "restart_intent_id": row.restart_intent_id},
                separators=(",", ":"),
            )
            if not deps.post_bus_turn(thread_id=lane_id, subject=subject, body=body):
                bus_ok = False

    ctx = dict(wake_plan.get("context") or {})
    ctx["turn_refs"] = row.turn_refs
    endpoint_ok = deps.endpoint_wake(
        holder_id=str(wake_plan["holder_id"]),
        lane_thread_id=row.lane_thread_id,
        wake_kind=str(wake_plan["wake_kind"]),
        context=ctx,
    )
    if bus_ok and endpoint_ok:
        store.mark_owed_wake_dispatched(row.row_id, outcome="dispatched")
        return True
    return False


def _plan_endpoint_wake(
    holder: dict[str, Any] | None,
    *,
    row: OwedWakeRow,
    resolve_superseded: Callable[[str], dict[str, Any] | None],
    lane_uses_bridge_wake: Callable[[str], bool] | None,
) -> dict[str, Any]:
    if holder is None:
        return {"skip": True, "reason": "held_no_live_seat"}
    state = str(holder.get("seat_state") or "")
    hid = str(holder.get("holder_id") or row.holder_id)
    if state == "released":
        return {"skip": True, "reason": "held_no_live_seat"}
    if state == "superseded":
        successor_reg = str(holder.get("superseded_by") or "").strip()
        if not successor_reg:
            return {"skip": True, "reason": "held_no_live_seat"}
        successor = resolve_superseded(successor_reg)
        if successor is None:
            return {"skip": True, "reason": "held_no_live_seat"}
        holder = successor
        hid = str(holder.get("holder_id") or hid)
        state = str(holder.get("seat_state") or "")

    lane = (row.lane_thread_id or holder.get("lane_thread_id") or "").strip()
    if lane and lane_uses_bridge_wake is not None and lane_uses_bridge_wake(lane):
        return {
            "holder_id": hid,
            "wake_kind": "bridge_wake",
            "context": {"lane_thread_id": lane, "holder": holder},
        }
    if state in {"driving", "dormant"}:
        return {
            "holder_id": hid,
            "wake_kind": "cse_followup",
            "context": {"holder": holder},
        }
    return {"skip": True, "reason": "held_no_live_seat"}


def encode_turn_refs(refs: list[dict[str, Any]]) -> str:
    return json.dumps(refs, separators=(",", ":"), sort_keys=True)


def decode_turn_refs(raw: Any) -> list[dict[str, Any]]:
    if raw is None or not str(raw).strip():
        return []
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def decode_arming_lanes(raw: Any) -> list[str]:
    if raw is None or not str(raw).strip():
        return []
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(x).strip() for x in parsed if str(x).strip()]
