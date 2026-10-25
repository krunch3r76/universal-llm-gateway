"""Operator observability ledger — owed_wake rows and endpoint wake producers.

Producer 1 (milestone): house-authored allowlisted subjects to maestro.
Producer 2 (drain_complete): restart_intent terminal CAS with arming_lanes.

No timer or poller; undispatched rows flush on the next gate read or post.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from universal_logging import get_logger

logger = get_logger(__name__)

PRODUCER1_MILESTONE_HEADS: frozenset[str] = frozenset(
    {
        "PLAN",
        "VERDICT",
        "LANDED",
        "DONE",
        "FAILED",
        "BLOCKED",
        "QUESTION",
        "RULING",
        "AMEND",
        "SEAT_REGISTRATION",
        "ESCALATED",
        "MILESTONE",
        "ASK",
        "READY",
    }
)
PRODUCER1_EXCLUDED_HEADS: frozenset[str] = frozenset(
    {
        "FACT",
        "PICKUP",
        "STATUS",
        "UPDATE",
        "HOP",
        "ROUTE",
        "ACK",
    }
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

PRODUCER1_TO_AGENTS: frozenset[str] = frozenset({"maestro", "web-anthropic"})
DEFAULT_MILESTONE_BACKSTOP_AGE_S = 900.0

OWED_WAKE_MAX_DISPATCH_ATTEMPTS = 5
OWED_WAKE_RETRY_BASE_S = 30
OWED_WAKE_RETRY_CAP_S = 240

_owed_wake_flush_task: asyncio.Task[None] | None = None
_OWED_WAKE_FLUSH_TASKS: set[asyncio.Task[None]] = set()


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


def producer1_to_agent_qualifies(to_agent: str) -> bool:
    """Producer 1 arms for house milestones addressed to maestro or web-anthropic."""
    return (to_agent or "").strip().lower() in PRODUCER1_TO_AGENTS


def milestone_backstop_min_age_s() -> float:
    raw = os.environ.get("OPERATOR_OBSERVABILITY_MILESTONE_BACKSTOP_MIN_AGE_S")
    if raw is None or not str(raw).strip():
        return DEFAULT_MILESTONE_BACKSTOP_AGE_S
    try:
        return max(float(raw.strip()), 0.0)
    except ValueError:
        return DEFAULT_MILESTONE_BACKSTOP_AGE_S


def _producer1_subject_head_token(subject: str) -> str:
    """First token before ``|`` (R3 milestone heads, e.g. ``PLAN r2|…`` → ``PLAN``)."""
    text = (subject or "").strip()
    if not text:
        return ""
    if "|" in text:
        head = text.split("|", 1)[0].strip().upper()
    else:
        head = text.strip().upper()
    return head.split()[0] if head else ""


def subject_qualifies_producer1(subject: str) -> bool:
    """True when the first subject token is an R3 waking milestone/exception head."""
    text = (subject or "").strip()
    if not text:
        return False
    token = _producer1_subject_head_token(text)
    if token in PRODUCER1_EXCLUDED_HEADS:
        return False
    return token in PRODUCER1_MILESTONE_HEADS


def is_chrome_bus_subject(subject: str) -> bool:
    """Chrome turns must never arm producer 1 (case-insensitive prefix only)."""
    text = (subject or "").strip()
    if not text:
        return True
    lowered = text.lower()
    return any(lowered.startswith(marker.lower()) for marker in _CHROME_SUBJECT_MARKERS)


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
    attempt_count: int = 0
    next_attempt_at: str | None = None


class BusPoster(Protocol):
    def __call__(self, *, thread_id: str, subject: str, body: str) -> bool: ...


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
    """Producer 1 — allowlisted house milestone to maestro or web-anthropic."""
    if not producer1_to_agent_qualifies(to_agent):
        return {"recorded": False, "reason": "not_to_producer1_recipient"}
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


def schedule_owed_wake_flush(*, store: Any | None = None) -> None:
    """Run owed-wake flush off the event loop (single-flight when a loop is running)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        maybe_flush_pending_owed_wakes(store=store)
        return

    global _owed_wake_flush_task
    if _owed_wake_flush_task is not None and not _owed_wake_flush_task.done():
        return

    async def _owed_wake_flush_worker() -> None:
        try:
            await asyncio.to_thread(maybe_flush_pending_owed_wakes, store=store)
        except Exception:
            logger.debug("owed-wake-flush task failed", exc_info=True)

    task = loop.create_task(_owed_wake_flush_worker(), name="owed-wake-flush")
    _owed_wake_flush_task = task
    _OWED_WAKE_FLUSH_TASKS.add(task)

    def _clear_leader(done: asyncio.Task[None]) -> None:
        global _owed_wake_flush_task
        if _owed_wake_flush_task is done:
            _owed_wake_flush_task = None

    task.add_done_callback(_OWED_WAKE_FLUSH_TASKS.discard)
    task.add_done_callback(_clear_leader)


def maybe_flush_pending_owed_wakes(*, store: Any | None = None) -> int:
    """Backstop flush for manage gate reads and posts; failures are non-fatal."""
    from .restart_intent_store import RestartIntentStore

    resolved = store if store is not None else RestartIntentStore.instance()
    try:
        from services.git_integration_worker.cse_session_holders import (
            build_owed_wake_deps,
        )

        return flush_pending_owed_wakes(resolved, deps=build_owed_wake_deps())
    except Exception:
        logger.debug("maybe_flush_pending_owed_wakes failed", exc_info=True)
        return 0


def _parse_turn_timestamp(raw: Any) -> datetime | None:
    if raw is None or not str(raw).strip():
        return None
    text = str(raw).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _turn_field(turn: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = turn.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _turn_is_stale_unread_producer1_candidate(
    turn: dict[str, Any], *, min_age_s: float, now: datetime
) -> bool:
    if turn.get("read_at") is not None:
        return False
    created = _parse_turn_timestamp(turn.get("created_at"))
    if created is None:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    if (now - created.astimezone(UTC)).total_seconds() < min_age_s:
        return False
    subject = _turn_field(turn, "subject")
    to_agent = _turn_field(turn, "to_agent", "to")
    from_agent = _turn_field(turn, "from_agent", "from")
    user_agent = turn.get("user_agent")
    if not producer1_to_agent_qualifies(to_agent):
        return False
    if is_chrome_bus_subject(subject):
        return False
    if not is_house_authored_turn(from_agent=from_agent, user_agent=user_agent):
        return False
    if not subject_qualifies_producer1(subject):
        return False
    return True


def _lanes_for_stale_projection() -> list[str]:
    from services.git_integration_worker.cse_session_holders import (
        _ledger_conn,
        ensure_schema,
    )

    lanes: list[str] = []
    try:
        with _ledger_conn() as conn:
            ensure_schema(conn)
            rows = conn.execute(
                "SELECT DISTINCT lane_thread_id FROM cse_session_holders "
                "WHERE lane_thread_id IS NOT NULL "
                "AND seat_state IN ('driving', 'dormant')"
            ).fetchall()
        for row in rows:
            lane = str(row["lane_thread_id"] or "").strip()
            if lane and lane not in lanes:
                lanes.append(lane)
    except Exception:
        logger.debug("lane list for stale projection failed", exc_info=True)
    return lanes


def _fetch_turns_for_lane(thread_id: str) -> list[dict[str, Any]]:
    lane = (thread_id or "").strip()
    if not lane:
        return []
    try:
        from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client

        from services.git_integration_worker.cse_session_holders import (
            agent_bus_request_headers,
        )

        headers = agent_bus_request_headers()
        with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=5.0) as client:
            resp = client.get(
                "/turns",
                params={"thread": lane, "last": 100, "compact": "true"},
                headers=headers or None,
            )
            if resp.status_code >= 400:
                return []
            payload = resp.json()
    except Exception:
        logger.debug(
            "fetch turns for stale projection failed lane=%s", lane, exc_info=True
        )
        return []
    turns = payload.get("turns") if isinstance(payload, dict) else None
    if not isinstance(turns, list):
        return []
    return [t for t in turns if isinstance(t, dict)]


def producer1_turn_already_dispatched(
    store: Any, *, holder_id: str, thread_id: str, turn_number: int
) -> bool:
    """True when this bus turn already has a dispatched owed_wake reference."""
    hid = (holder_id or "").strip()
    lane = (thread_id or "").strip()
    if not hid or not lane:
        return False
    try:
        with store._connect() as conn:
            rows = conn.execute(
                "SELECT turn_refs, dispatched_at FROM owed_wake WHERE holder_id=?",
                (hid,),
            ).fetchall()
    except Exception:
        logger.debug("producer1_turn_already_dispatched lookup failed", exc_info=True)
        return False
    for row in rows:
        if row["dispatched_at"] is None:
            continue
        for ref in decode_turn_refs(row["turn_refs"]):
            if str(ref.get("thread_id") or "") == lane and int(
                ref.get("turn_number") or -1
            ) == int(turn_number):
                return True
    return False


def project_stale_unread_milestone_owed_wakes(store: Any) -> int:
    """Lazy backstop: project unread milestones older than the threshold into owed_wake."""
    from services.git_integration_worker.cse_session_holders import (
        resolve_maestro_holder_id_for_lane,
    )

    min_age_s = milestone_backstop_min_age_s()
    now = datetime.now(UTC)
    projected = 0
    for lane in _lanes_for_stale_projection():
        holder_id = resolve_maestro_holder_id_for_lane(lane)
        if not holder_id:
            continue
        for turn in _fetch_turns_for_lane(lane):
            if not _turn_is_stale_unread_producer1_candidate(
                turn, min_age_s=min_age_s, now=now
            ):
                continue
            turn_number = turn.get("turn_number")
            if turn_number is None:
                continue
            if producer1_turn_already_dispatched(
                store,
                holder_id=holder_id,
                thread_id=lane,
                turn_number=int(turn_number),
            ):
                continue
            out = maybe_producer1_milestone(
                store,
                subject=_turn_field(turn, "subject"),
                to_agent=_turn_field(turn, "to_agent", "to"),
                from_agent=_turn_field(turn, "from_agent", "from"),
                thread_id=lane,
                turn_number=int(turn_number),
                holder_id=holder_id,
                lane_thread_id=lane,
                user_agent=turn.get("user_agent"),
                deps=None,
            )
            if out.get("recorded"):
                projected += 1
    return projected


def _owed_wake_retry_due(next_attempt_at: str | None, *, now: datetime) -> bool:
    if next_attempt_at is None or not str(next_attempt_at).strip():
        return True
    parsed = _parse_turn_timestamp(next_attempt_at)
    if parsed is None:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return now >= parsed.astimezone(UTC)


def flush_pending_owed_wakes(store: Any, *, deps: OwedWakeDeps) -> int:
    """Backstop: project stale unread milestones, then retry dispatch for open rows."""
    try:
        project_stale_unread_milestone_owed_wakes(store)
    except Exception:
        logger.debug("stale milestone projection failed", exc_info=True)
    count = 0
    now = datetime.now(UTC)
    for row in store.list_open_undispatched_owed_wakes():
        if row.producer == PRODUCER_MILESTONE and not producer1_cse_dispatch_enabled():
            continue
        if not _owed_wake_retry_due(row.next_attempt_at, now=now):
            continue
        if attempt_dispatch(store, row, deps=deps):
            count += 1
            continue
        new_count = store.record_owed_wake_failure(row.row_id)
        if new_count >= OWED_WAKE_MAX_DISPATCH_ATTEMPTS:
            store.mark_owed_wake_dispatched(row.row_id, outcome="held_retry_exhausted")
        else:
            delay_s = min(
                OWED_WAKE_RETRY_BASE_S * (2 ** (new_count - 1)),
                OWED_WAKE_RETRY_CAP_S,
            )
            failed_at = datetime.now(UTC)
            next_at = (failed_at + timedelta(seconds=delay_s)).isoformat()
            store.set_owed_wake_next_attempt(row.row_id, next_at)
    return count


def _turn_ref_already_read(thread_id: str, turn_number: int) -> bool:
    """True when the bus turn already has read_at before owed_wake delivery."""
    lane = (thread_id or "").strip()
    if not lane:
        return False
    try:
        from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client

        from services.git_integration_worker.cse_session_holders import (
            agent_bus_request_headers,
        )

        headers = agent_bus_request_headers()
        with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=5.0) as client:
            resp = client.get(
                "/turns/by-number",
                params={"thread": lane, "turn_number": int(turn_number)},
                headers=headers or None,
            )
            if resp.status_code >= 400:
                return False
            payload = resp.json()
    except Exception:
        logger.debug(
            "turn read_at probe failed thread=%s turn=%s",
            thread_id,
            turn_number,
            exc_info=True,
        )
        return False
    return payload.get("read_at") is not None


def _turn_refs_all_read(refs: list[dict[str, Any]]) -> bool:
    if not refs:
        return False
    for ref in refs:
        thread_id = str(ref.get("thread_id") or "").strip()
        try:
            turn_number = int(ref.get("turn_number"))
        except (TypeError, ValueError):
            return False
        if not _turn_ref_already_read(thread_id, turn_number):
            return False
    return True


def _endpoint_wake_already_delivered(refs: list[dict[str, Any]], *, agent: str) -> bool:
    from agent_bus_store.db.connection import connect
    from agent_bus_store.db.endpoint_wake_ledger import endpoint_wake_covers_turn

    try:
        with connect() as conn:
            for ref in refs:
                thread_id = str(ref.get("thread_id") or "").strip()
                try:
                    turn_number = int(ref.get("turn_number"))
                except (TypeError, ValueError):
                    continue
                if endpoint_wake_covers_turn(
                    conn, agent=agent, thread_id=thread_id, turn_number=turn_number
                ):
                    return True
    except Exception:
        logger.debug("endpoint_wake dedupe probe failed", exc_info=True)
    return False


def attempt_dispatch(store: Any, row: OwedWakeRow, *, deps: OwedWakeDeps) -> bool:
    """Post bus turns and run endpoint wake; leave dispatched_at null on failure."""
    if row.closed_at is not None or row.dispatched_at is not None:
        return row.dispatched_at is not None

    if _turn_refs_all_read(row.turn_refs):
        store.mark_owed_wake_dispatched(row.row_id, outcome="skipped")
        return True

    wake_agent = "maestro"
    if _endpoint_wake_already_delivered(row.turn_refs, agent=wake_agent):
        store.mark_owed_wake_dispatched(row.row_id, outcome="skipped")
        return True

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
    lanes = row.arming_lanes or ([row.lane_thread_id] if row.lane_thread_id else [])
    if row.producer == PRODUCER_DRAIN_COMPLETE:
        for lane in lanes:
            lane_id = str(lane).strip()
            if not lane_id:
                continue
            subject = (
                f"FACT|restart_intent terminal — owed wake ({row.restart_intent_id})"
            )
            body = json.dumps(
                {
                    "owed_wake_row_id": row.row_id,
                    "restart_intent_id": row.restart_intent_id,
                },
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
        try:
            from agent_bus_store.db.endpoint_wake_ledger import (
                suppress_endpoint_wake_for_turn,
            )

            for ref in row.turn_refs:
                thread_id = str(ref.get("thread_id") or "").strip()
                try:
                    turn_number = int(ref.get("turn_number"))
                except (TypeError, ValueError):
                    continue
                if thread_id:
                    suppress_endpoint_wake_for_turn(
                        agent=wake_agent,
                        thread_id=thread_id,
                        turn_number=turn_number,
                    )
        except Exception:
            logger.debug("suppress endpoint wake after owed dispatch failed", exc_info=True)
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
