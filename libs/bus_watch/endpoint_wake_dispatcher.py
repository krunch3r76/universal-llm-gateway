"""Durable-cursor wake dispatcher hosted by the agent_bus process."""

from __future__ import annotations

import asyncio
import json
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
    r"admitted|progress|score_resurface|park-harvest|hop-[a-z0-9]+)(?:\b|\||$)"
)
_POLL_S = 2.0


def subject_allowed(subject: str | None, *, cse: bool) -> bool:
    """Wake envelope: CSE or Grok allowlist, plus TYPE relays."""
    text = subject or ""
    if _INTERMEDIATE_SUBJECT_RE.search(text):
        return False
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
    if not subject_allowed(subject, cse=cse):
        return False
    return True


def _window_binding_route(agent: str) -> tuple[str, dict[str, Any]] | None:
    """Synthetic grok subscription from the runtime routing file."""
    from cursor_bridge.grok_window_bindings import binding_for

    row = binding_for(agent)
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
    return {
        "agent": normalize_bus_address(str(turn["to_agent"])),
        "thread_id": str(turn["thread"]),
        "turn_id": int(turn["id"]),
        "turn_number": int(turn["turn_number"]),
    }


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
        agent = normalize_bus_address(raw_to)
        thread = str(turn["thread"])
        if agent == "web-anthropic":
            resolved = None
            cse = True
        else:
            bound = _window_binding_route(raw_to)
            resolved = bound if bound is not None else store.subscription(agent, thread)
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
    row: dict[str, Any], sub: dict[str, Any], turn_number: int
) -> tuple[str, str]:
    options = {
        "display": sub.get("display"),
        "app": sub.get("surface"),
        "thread": row["thread_id"],
        "title": sub.get("title"),
        "agent_id": sub.get("agent_id"),
        "select": True,
        "turn": turn_number,
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
    resolved = store.subscription(agent, thread)
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
    lowest = min(int(r["turn_number"]) for r in rows)
    state, reason = _display_post(rows[0], sub, lowest)
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
    store = StoreClient()
    idle: dict[str, float] = {}
    while True:
        await asyncio.to_thread(scan_once, store, idle_mark=idle)
        await asyncio.to_thread(fire_due_wakes, store)
        await asyncio.sleep(_POLL_S)
