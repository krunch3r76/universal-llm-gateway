"""Steer inject rung-1 — deposit, spool, ack, idle escalation (GIW authority).

Authority = worker-thread agent-bus turn (durable deposit). Projection = per-dispatch
spool file the stdio bridge reads. Escalation = park+resume when delivery never acks
on MCP tool-call idle ([universal:obs-over-timeouts]).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client
from universal_event_bus import Event, event_factory
from scripts.mcp_bridge_steer_inject import (
    append_spool_entry,
    read_delivery_ack,
    spool_path,
)
from services.git_integration_worker.cursor_sdk_context import steer_spool_dir
from services.git_integration_worker.cursor_sdk_events import emit_frontier_event
from services.git_integration_worker.cursor_sdk_park_for_restart import (
    ParkSignalResult,
    signal_park,
)

DEFAULT_TTL_S = 300
_STEER_FROM = "git-integration-worker"
_STEER_SUBJECT = "STEER directive"


@dataclass(frozen=True, slots=True)
class SteerDepositResult:
    dispatch_id: str
    entry_id: str
    authority_turn_id: str
    spool_path: str


@event_factory
def SdkSteerInjectRequested(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    entry_id: str,
    ttl_s: int,
    actor: str,
) -> Event:
    return Event(
        signal="frontier.sdk.steer.inject.requested",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "entry_id": entry_id,
            "ttl_s": ttl_s,
            "actor": actor,
        },
        scope="node",
    )


@event_factory
def SdkSteerInjectSpooled(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    entry_id: str,
    authority_turn_id: str,
    spool_uri: str,
) -> Event:
    return Event(
        signal="frontier.sdk.steer.inject.spooled",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "entry_id": entry_id,
            "authority_turn_id": authority_turn_id,
            "spool_uri": spool_uri,
        },
        scope="node",
    )


@event_factory
def SdkSteerInjectDelivered(  # noqa: N802
    dispatch_id: str,
    entry_id: str,
    authority_turn_id: str,
) -> Event:
    return Event(
        signal="frontier.sdk.steer.inject.delivered",
        payload={
            "dispatch_id": dispatch_id,
            "entry_id": entry_id,
            "authority_turn_id": authority_turn_id,
        },
        scope="node",
    )


@event_factory
def SdkSteerInjectExpired(  # noqa: N802
    dispatch_id: str,
    entry_id: str,
    ttl_s: int,
) -> Event:
    return Event(
        signal="frontier.sdk.steer.inject.expired",
        payload={"dispatch_id": dispatch_id, "entry_id": entry_id, "ttl_s": ttl_s},
        scope="node",
    )


@event_factory
def SdkSteerInjectEscalated(  # noqa: N802
    dispatch_id: str,
    entry_id: str,
    reason: str,
) -> Event:
    return Event(
        signal="frontier.sdk.steer.inject.escalated",
        payload={"dispatch_id": dispatch_id, "entry_id": entry_id, "reason": reason},
        scope="node",
    )


def _bus_headers() -> dict[str, str]:
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _deposit_authority_turn(
    *,
    thread_id: str,
    dispatch_id: str,
    directive: str,
    reason: str,
    actor: str,
) -> str:
    body = json.dumps(
        {
            "dispatch_id": dispatch_id,
            "directive": directive,
            "reason": reason,
            "actor": actor,
        },
        separators=(",", ":"),
    )
    payload = {
        "thread": thread_id,
        "from": _STEER_FROM,
        "to": f"cursor-sdk:dispatch:{dispatch_id}",
        "subject": _STEER_SUBJECT,
        "body": body,
        "status": "open",
    }
    with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=15.0) as client:
        resp = client.post("/turns", json=payload, headers=_bus_headers())
    if resp.status_code >= 400:
        raise RuntimeError(
            f"agent-bus STEER deposit failed: HTTP {resp.status_code} {resp.text[:200]}"
        )
    try:
        parsed = resp.json()
    except ValueError as exc:
        raise RuntimeError("agent-bus STEER deposit returned non-JSON") from exc
    turn = parsed.get("turn_number") or parsed.get("id")
    if turn is None:
        raise RuntimeError(f"agent-bus STEER deposit missing turn id: {parsed!r}")
    return str(turn)


def deposit_steer_directive(
    *,
    dispatch_id: str,
    thread_id: str,
    directive: str,
    reason: str,
    actor: str = "steer-inject",
    ttl_s: int = DEFAULT_TTL_S,
    spool_dir: Path | None = None,
) -> SteerDepositResult:
    """Deposit authority turn + spool entry; emit requested/spooled events."""
    if not dispatch_id or not thread_id or not directive.strip():
        raise ValueError("dispatch_id, thread_id, and directive are required")
    root = spool_dir or steer_spool_dir()
    entry_id = os.urandom(8).hex()
    emit_frontier_event(
        SdkSteerInjectRequested(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            entry_id=entry_id,
            ttl_s=ttl_s,
            actor=actor,
        )
    )
    authority_turn_id = _deposit_authority_turn(
        thread_id=thread_id,
        dispatch_id=dispatch_id,
        directive=directive,
        reason=reason,
        actor=actor,
    )
    append_spool_entry(
        dispatch_id,
        authority_turn_id=authority_turn_id,
        directive=directive,
        ttl_s=ttl_s,
        spool_dir=root,
        entry_id=entry_id,
    )
    uri = str(spool_path(root, dispatch_id))
    emit_frontier_event(
        SdkSteerInjectSpooled(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            entry_id=entry_id,
            authority_turn_id=authority_turn_id,
            spool_uri=uri,
        )
    )
    return SteerDepositResult(
        dispatch_id=dispatch_id,
        entry_id=entry_id,
        authority_turn_id=authority_turn_id,
        spool_path=uri,
    )


def poll_delivery_ack(
    deposit: SteerDepositResult,
    *,
    spool_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Return delivered spool row and emit ``…inject.delivered`` when acked."""
    root = spool_dir or steer_spool_dir()
    row = read_delivery_ack(
        deposit.dispatch_id, deposit.entry_id, spool_dir=root
    )
    if row is None:
        return None
    emit_frontier_event(
        SdkSteerInjectDelivered(
            dispatch_id=deposit.dispatch_id,
            entry_id=deposit.entry_id,
            authority_turn_id=deposit.authority_turn_id,
        )
    )
    return row


def escalate_idle_to_park(
    deposit: SteerDepositResult,
    *,
    reason: str,
    actor: str = "steer-inject",
    intent_id: str | None = None,
) -> ParkSignalResult:
    """Rung-2: idle absence of bridge delivery ack ⇒ park+resume ladder."""
    emit_frontier_event(
        SdkSteerInjectEscalated(
            dispatch_id=deposit.dispatch_id,
            entry_id=deposit.entry_id,
            reason=reason,
        )
    )
    return signal_park(
        deposit.dispatch_id,
        intent_id=intent_id,
        drain_epoch=None,
        actor=actor,
        reason=reason,
    )


def expire_undelivered(
    deposit: SteerDepositResult,
    *,
    ttl_s: int,
) -> None:
    """Emit expired when spool TTL elapses without bridge delivery."""
    emit_frontier_event(
        SdkSteerInjectExpired(
            dispatch_id=deposit.dispatch_id,
            entry_id=deposit.entry_id,
            ttl_s=ttl_s,
        )
    )
