"""Durable-cursor wake dispatcher hosted by the agent_bus process."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx
from agent_seat.registry import STAGE_MANAGER_ADDRESSES, normalize_bus_address
from claude_bundles.holder_strings import holder_id_from_chat_url
from transport_utils import DEFAULT_STARGATE_URL, make_sync_client

from bus_watch.endpoint_wake_transport import (
    StoreClient,
    classify_cse,
    classify_display,
)

logger = logging.getLogger(__name__)

CSE_WAKE_SUBJECT_HEADS = frozenset(
    {
        "CONSULT",
        "QUESTION",
        "ASK",
        "REQUEST",
        "AMEND",
        "DONE",
        "FAILED",
        "FACT",
        "REVIEW",
        "PLAN",
        "LANDED",
        "VERDICT",
        "RESULT",
        "REPORT",
        "RETURN",
        "NUDGE",
        "SYNTHESIS",
        "CORRECTION",
        "RESTARTED",
        "GATE MISS",
        "ESCALATE",
        "BLOCKED",
        "READY",
    }
)
GROK_WAKE_SUBJECT_HEADS = frozenset(
    {
        "REQUEST",
        "REVIEW-REQUEST",
        "RULING",
        "ACK",
        "AMEND",
        "PARK",
        "CANCEL",
        "WITHDRAW",
        "COMMISSION",
        "DECOMMISSION",
        "WAKE",
        "CONSULT",
        "QUESTION",
        "ASK",
        "DONE",
        "FAILED",
        "FACT",
        "REVIEW",
        "PLAN",
        "LANDED",
        "VERDICT",
        "RESULT",
        "REPORT",
        "RETURN",
        "NUDGE",
        "RELAY",
        "SEND",
        "PASS",
        "SYNTHESIS",
        "CORRECTION",
        "RESTARTED",
        "GATE MISS",
        "ESCALATE",
        "BLOCKED",
        "READY",
    }
)
ENDPOINT_WAKE_SUBJECT_HEADS = CSE_WAKE_SUBJECT_HEADS
_ROUTE_CONTROL_TYPE = "TYPE: ROUTE/CONTROL"
_INTERMEDIATE_SUBJECT_RE = re.compile(
    r"(?i)(?:^|\b|\|)(?:pickup|cc\s+pickup|house-cp|checkpoint|in-flight|in_flight|"
    r"admitted|progress|park-harvest wake|hop-[a-z0-9]+)(?:\b|\||$)"
)
_CURSOR_GATE_WAKE_RE = re.compile(
    r"(?i)(?:CLOSEOUT|VERDICT|SCORE_RESURFACE|park-harvest escalation|conductor-hop escalation|"
    r"PARKED_TRANSPORT|BLOCKED|ESCALATE|HOLD_MERGE|OPERATOR_GATE|"
    r"cdp (?:reply|FAILED|UNVERIFIED|DELIVERY FAILED)\b)"
)
_ROUTINE_DISPATCH_FROM = frozenset({"cursor-sdk", "conductor-hop", "dispatch"})
_PEER_WAKE_FROM = frozenset(
    {
        "grok-bot",
        "insyncerator",
        "syncerator",
        "impresario",
        "web-anthropic",
        "maestro",
        "cursor",
    }
)
_POLL_S = 2.0


def _effective_to_agent(to_agent: str | None, subject: str | None = None) -> str:
    """Route child-lane gate wakes addressed to dispatch/conductor-hop to cursor."""
    agent = normalize_bus_address(str(to_agent or ""))
    text = str(subject or "")
    if agent == "dispatch" and _CURSOR_GATE_WAKE_RE.search(text):
        return "cursor"
    if agent == "conductor-hop" and _CURSOR_GATE_WAKE_RE.search(text):
        if "contract=conductor" in text.lower() and "caller=conductor-hop" in text.lower():
            return agent
        return "cursor"
    return agent


def peer_operator_wake_from(from_agent: str) -> bool:
    """Operator / Grok / Maestro seats always enqueue wakes; routine dispatch chatter does not."""
    if from_agent in _ROUTINE_DISPATCH_FROM:
        return False
    if from_agent in _PEER_WAKE_FROM:
        return True
    if from_agent.startswith("grok-bot-"):
        return True
    if from_agent.endswith("-impresario"):
        return True
    return False


def subject_allowed(
    subject: str | None, *, cse: bool, to_agent: str | None = None
) -> bool:
    """Wake envelope: CSE or Grok allowlist, plus TYPE relays."""
    text = subject or ""
    agent = _effective_to_agent(to_agent, text)
    if _INTERMEDIATE_SUBJECT_RE.search(text):
        if agent == "cursor" and _CURSOR_GATE_WAKE_RE.search(text):
            return True
        return False
    if agent == "cursor" and _CURSOR_GATE_WAKE_RE.search(text):
        return True
    if text.startswith("TYPE: DIRECTIVE"):
        return True
    if not cse and text.startswith(_ROUTE_CONTROL_TYPE):
        return True
    allowed = CSE_WAKE_SUBJECT_HEADS if cse else GROK_WAKE_SUBJECT_HEADS
    head = text.split("|", 1)[0].strip().upper()
    if head in allowed:
        return True
    if "|" in text and head:
        first_token = head.split()[0]
        if first_token in allowed:
            return True
    return False


def qualifies(turn: dict[str, Any], *, cse: bool) -> bool:
    """Fork-1 rule 1, plus wake envelope allowlists on every routed turn."""
    to_agent = normalize_bus_address(str(turn.get("to_agent") or ""))
    from_agent = normalize_bus_address(str(turn.get("from_agent") or ""))
    if to_agent in {"all", "team"} or not to_agent or to_agent == from_agent:
        return False
    if to_agent in STAGE_MANAGER_ADDRESSES and from_agent in STAGE_MANAGER_ADDRESSES:
        return False
    subject = str(turn.get("subject") or "")
    head = subject.split("|", 1)[0].strip().upper()
    if (
        to_agent in STAGE_MANAGER_ADDRESSES
        or to_agent in {"grok-bot", "web-anthropic", "maestro"}
    ) and head in {"ACK", "ROUTE"}:
        return False
    if not peer_operator_wake_from(from_agent) and not subject_allowed(
        subject, cse=cse, to_agent=to_agent
    ):
        return False
    return True


def _grok_binding_row(agent: str) -> dict[str, Any] | None:
    """Live window row when ``agent`` follows grok-window routing."""
    from cursor_bridge.grok_window_bindings import binding_for

    for candidate in (agent, normalize_bus_address(agent)):
        row = binding_for(candidate)
        if row is not None:
            return row
    return None


def _apply_grok_window_overlay(
    agent: str, resolved: tuple[str, dict[str, Any]]
) -> tuple[str, dict[str, Any]]:
    """Subscription DB rows can lag promote; display always follows routing file."""
    row = _grok_binding_row(agent)
    if row is None:
        return resolved
    route, sub = resolved
    merged = dict(sub)
    merged["display"] = row["display"]
    return route, merged


def _window_binding_route(agent: str) -> tuple[str, dict[str, Any]] | None:
    """Synthetic grok subscription from the runtime routing file."""
    row = _grok_binding_row(agent)
    if row is None:
        return None
    return (
        "window-binding",
        {
            "surface": "grok",
            "display": row["display"],
            "title": row.get("title") or "",
            "agent_id": agent,
            "state": "subscribed",
        },
    )


def _pending_item(turn: dict[str, Any]) -> dict[str, Any]:
    subject = str(turn.get("subject") or "")
    return {
        "agent": _effective_to_agent(turn.get("to_agent"), subject),
        "thread_id": str(turn["thread"]),
        "turn_id": int(turn["id"]),
        "turn_number": int(turn["turn_number"]),
    }


def _resolve_delivery_route(
    store: StoreClient, agent: str, thread: str, *, raw_to: str | None = None
) -> tuple[str, dict[str, Any]] | None:
    """Subscription row, else legacy alias, else synthetic grok window binding."""
    resolved = store.subscription(agent, thread)
    if resolved is not None:
        return _apply_grok_window_overlay(agent, resolved)
    from cursor_bridge.grok_window_bindings import mailbox_members

    for member in mailbox_members(agent):
        if member == agent:
            continue
        resolved = store.subscription(member, thread)
        if resolved is not None:
            return _apply_grok_window_overlay(agent, resolved)
    for candidate in (raw_to, agent):
        if not candidate:
            continue
        bound = _window_binding_route(str(candidate))
        if bound is not None:
            return bound
    return None


def scan_once(store: StoreClient, *, idle_mark: dict[str, float] | None = None) -> None:
    """Read new turns and CAS pending rows. Store and GIW errors propagate."""
    import time

    cursor = store.cursor()
    high = int(cursor["high_water_turn_id"])
    turns = store.since(high)
    if not turns:
        return
    pending: list[dict[str, Any]] = []
    for turn in turns:
        raw_to = str(turn.get("to_agent") or "")
        subject = str(turn.get("subject") or "")
        agent = _effective_to_agent(raw_to, subject)
        thread = str(turn["thread"])
        if agent == "web-anthropic":
            resolved = None
            cse = True
        else:
            resolved = _resolve_delivery_route(store, agent, thread, raw_to=raw_to)
            cse = (
                resolved is not None and str(resolved[1].get("surface") or "") == "cse"
            )
        if not qualifies(turn, cse=cse):
            continue
        if agent == "web-anthropic":
            if not store.holder(thread):
                continue
        elif resolved is not None and str(resolved[1].get("surface") or "") == "cse":
            lane = str(resolved[1].get("agent_id") or "").strip()
            if not lane or not store.holder(lane):
                continue
        elif resolved is None:
            continue
        pending.append(_pending_item(turn))
    new_hw = int(turns[-1]["id"])
    if not pending:
        mark = idle_mark if idle_mark is not None else {}
        if time.monotonic() - mark.get("at", 0.0) < 60:
            return
        mark["at"] = time.monotonic()
    store.scan(high, new_hw, pending)


def _display_post(
    row: dict[str, Any], sub: dict[str, Any], turn: int | str
) -> tuple[str, str]:
    options = {
        "display": sub.get("display"),
        "app": sub.get("surface"),
        "thread": row["thread_id"],
        "title": sub.get("title"),
        "agent_id": sub.get("agent_id"),
        "select": True,
        "turn": turn,
    }
    body = {
        "model": "display-chat-wake",
        "messages": [{"role": "user", "content": "wake"}],
        "pipeline_options": options,
    }
    try:
        with make_sync_client(DEFAULT_STARGATE_URL, timeout=30.0) as client:
            response = client.post(
                "/v1/chat/completions",
                json=body,
                headers={"X-ULG-Surface": "code"},
            )
        parsed: Any
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            parsed = json.loads(content) if isinstance(content, str) else content
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError):
            parsed = None
        return classify_display(response.status_code, parsed, None)
    except httpx.ConnectError as exc:
        return classify_display(0, None, exc)
    except Exception as exc:  # noqa: BLE001 — table classifies; never raise
        return "unknown", type(exc).__name__


def _cse_agree(holder: dict[str, Any], dry: dict[str, Any]) -> bool:
    left = str(holder.get("registration_id") or "")
    right = str(dry.get("registration_id") or "")
    if left and right:
        return left == right
    a = holder_id_from_chat_url(str(holder.get("chat_url") or ""))
    b = holder_id_from_chat_url(str(dry.get("url") or dry.get("chat_url") or ""))
    return bool(a and a == b)


def _deliver_cse(
    rows: list[dict[str, Any]],
    holder: dict[str, Any],
    *,
    target_lane: str | None = None,
) -> tuple[str, str, str | None]:
    from bus_watch.cse_followup import fire_cse_followup

    thread = str(rows[0]["thread_id"])
    lane = str(target_lane or thread).strip() or thread
    numbers = ", ".join(str(r["turn_number"]) for r in rows)
    if thread != lane:
        induction = f"wake thread {thread} turns {numbers}"
    else:
        induction = f"wake turns {numbers}"
    fallback_url = holder.get("chat_url")
    fallback_reg = holder.get("registration_id")
    dry = fire_cse_followup(
        induction,
        lane,
        dry_run=True,
        fallback_chat_url=str(fallback_url) if fallback_url else None,
        fallback_registration_id=str(fallback_reg) if fallback_reg else None,
    )
    if dry.get("error") == "no_identity" and dry.get("source") == "unavailable":
        return "pending", "connect", None
    if not _cse_agree(holder, dry):
        return (
            "skipped",
            f"cse_authority_split giw={holder.get('registration_id')} cdp={dry.get('registration_id')}",
            None,
        )
    live = fire_cse_followup(
        induction,
        lane,
        dry_run=False,
        fallback_chat_url=str(fallback_url) if fallback_url else None,
        fallback_registration_id=str(fallback_reg) if fallback_reg else None,
    )
    state, reason = classify_cse(live if isinstance(live, dict) else {})
    return state, reason, str(live.get("registration_id") or "") or None


def fire_due_wakes(store: StoreClient) -> dict[str, Any] | None:
    """Claim one group and deliver it. Stargate and cdp-ask stay in the table."""
    claimed = store.claim()
    rows = [r for r in claimed.get("rows") or [] if r.get("state") == "claimed"]
    wake_id = claimed.get("wake_id")
    if not wake_id or not rows:
        return claimed
    agent = rows[0]["agent"]
    thread = rows[0]["thread_id"]
    if agent == "web-anthropic":
        try:
            holder = store.holder(thread)
        except Exception as exc:
            store.result(
                wake_id=wake_id,
                outcome="unknown",
                reason=type(exc).__name__,
                route="cse",
                subscription_id=None,
                cse_registration_id=None,
            )
            return claimed
        if not holder:
            store.result(
                wake_id=wake_id,
                outcome="skipped",
                reason="no_route",
                route=None,
                subscription_id=None,
                cse_registration_id=None,
            )
            return claimed
        state, reason, reg = _deliver_cse(rows, holder, target_lane=thread)
        store.result(
            wake_id=wake_id,
            outcome=state,
            reason=reason,
            route="cse",
            subscription_id=None,
            cse_registration_id=reg,
        )
        return claimed
    resolved = _resolve_delivery_route(store, agent, thread)
    if resolved is None:
        store.result(
            wake_id=wake_id,
            outcome="skipped",
            reason="no_route",
            route=None,
            subscription_id=None,
            cse_registration_id=None,
        )
        return claimed
    route, sub = resolved
    if str(sub.get("surface") or "") == "cse":
        lane = str(sub.get("agent_id") or "").strip()
        if not lane:
            store.result(
                wake_id=wake_id,
                outcome="skipped",
                reason="no_route",
                route=None,
                subscription_id=None,
                cse_registration_id=None,
            )
            return claimed
        try:
            holder = store.holder(lane)
        except Exception as exc:
            store.result(
                wake_id=wake_id,
                outcome="unknown",
                reason=type(exc).__name__,
                route="cse",
                subscription_id=sub.get("id"),
                cse_registration_id=None,
            )
            return claimed
        if not holder:
            store.result(
                wake_id=wake_id,
                outcome="skipped",
                reason="no_route",
                route=route,
                subscription_id=sub.get("id"),
                cse_registration_id=None,
            )
            return claimed
        state, reason, reg = _deliver_cse(rows, holder, target_lane=lane)
        store.result(
            wake_id=wake_id,
            outcome=state,
            reason=reason,
            route="cse",
            subscription_id=sub.get("id"),
            cse_registration_id=reg,
        )
        return claimed
    turn_numbers = sorted({int(r["turn_number"]) for r in rows})
    turn_arg: int | str = (
        turn_numbers[0] if len(turn_numbers) == 1 else ",".join(str(n) for n in turn_numbers)
    )
    state, reason = _display_post(rows[0], sub, turn_arg)
    store.result(
        wake_id=wake_id,
        outcome=state,
        reason=reason,
        route=route,
        subscription_id=sub.get("id"),
        cse_registration_id=None,
    )
    return claimed


async def run_dispatcher() -> None:
    """Scan and fire until cancelled. Each pass runs on a worker thread."""
    from bus_watch.ide_context_hop_monitor import check_cursor_ide_context_hops

    store = StoreClient()
    idle: dict[str, float] = {}
    hop_mark: dict[str, float] = {}
    while True:
        try:
            await asyncio.to_thread(scan_once, store, idle_mark=idle)
            await asyncio.to_thread(fire_due_wakes, store)
            await asyncio.to_thread(
                check_cursor_ide_context_hops, store, mark=hop_mark
            )
        except Exception:
            logger.exception("endpoint_wake_dispatcher pass failed")
        await asyncio.sleep(_POLL_S)
