"""Durable-cursor wake dispatcher hosted by the agent_bus process."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
from agent_bus_store.routes.threads.send_prep import MAESTRO_MILESTONE_PREFIXES
from agent_seat.registry import STAGE_MANAGER_ADDRESSES, normalize_bus_address
from claude_bundles.holder_strings import holder_id_from_chat_url
from cursor_bridge.chat_target import exchange_lines
from transport_utils import DEFAULT_STARGATE_URL, make_sync_client

from bus_watch.endpoint_wake_transport import (
    StoreClient,
    classify_cse,
    classify_display,
)
from bus_watch.ide_hop_land import expire_overdue_hop, expire_overdue_hops

# ``ACK`` is in ``MAESTRO_MILESTONE_PREFIXES`` so compose keeps ``ACK|`` on maestro;
# ``qualifies()`` suppresses ``ACK|`` wakes to maestro / ``web-anthropic``.
_MAESTRO_MILESTONE_WAKE_HEADS = MAESTRO_MILESTONE_PREFIXES

logger = logging.getLogger(__name__)

_no_composer_streaks: dict[tuple[str, str], int] = {}
_display_reachable_since: float | None = None


def _reset_no_composer_state() -> None:
    """Clear in-process no_composer streak and display-reachable clock (test isolation)."""
    _no_composer_streaks.clear()
    global _display_reachable_since
    _display_reachable_since = None


def _note_display_post_reason(reason: str) -> float:
    """Update display-reachable clock; return display-reachable age in seconds."""
    global _display_reachable_since
    if reason in {"debug_unreachable", "connect"}:
        _display_reachable_since = None
        return 0.0
    now = time.monotonic()
    if _display_reachable_since is None:
        _display_reachable_since = now
    return now - _display_reachable_since


def _cursor_no_composer_subscription_key(
    sub: dict[str, Any], thread: str
) -> tuple[str, str] | None:
    if str(sub.get("surface") or "") != "cursor":
        return None
    dead_agent_id = str(sub.get("agent_id") or "").strip()
    scope = str(sub.get("thread_scope") or thread).strip()
    if not dead_agent_id or scope in {"", "*"}:
        return None
    return dead_agent_id, scope


def _update_no_composer_streak_and_gate_unsubscribe(
    sub: dict[str, Any],
    thread: str,
    reason: str,
    *,
    display_reachable_age: float,
) -> bool:
    """Update streak state; return True when unsubscribe gate is met."""
    key_tuple = _cursor_no_composer_subscription_key(sub, thread)
    if key_tuple is None:
        return False
    key = key_tuple
    if reason != "no_composer":
        _no_composer_streaks.pop(key, None)
        return False
    streak = _no_composer_streaks.get(key, 0) + 1
    _no_composer_streaks[key] = streak
    gate = streak >= 2 or (
        _display_reachable_since is not None and display_reachable_age >= 30.0
    )
    if gate:
        _no_composer_streaks.pop(key, None)
    return gate


CSE_WAKE_SUBJECT_HEADS = frozenset(
    {
        "CONSULT",
        "QUESTION",
        "ASK",
        "REQUEST",
        "AMEND",
        "DONE",
        "FAILED",
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
        "EAR",
        "RULING",
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
        "EAR",
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
    r"cdp (?:reply|FAILED|UNVERIFIED|DELIVERY FAILED)\b|"
    r"Quiet with work in flight)"
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
    """Route gate-wake subjects to cursor.

    Addresses in {dispatch, conductor-hop, git-integration-worker, frontier-admit}
    whose subject matches `_CURSOR_GATE_WAKE_RE` return cursor, except a
    conductor-hop subject that contains contract=conductor and caller=conductor-hop
    and does not contain degraded.
    """
    agent = normalize_bus_address(str(to_agent or ""))
    text = str(subject or "")
    if agent in {
        "dispatch",
        "conductor-hop",
        "git-integration-worker",
        "frontier-admit",
    } and _CURSOR_GATE_WAKE_RE.search(text):
        lowered = text.lower()
        if (
            agent == "conductor-hop"
            and "contract=conductor" in lowered
            and "caller=conductor-hop" in lowered
            and "degraded" not in lowered
        ):
            return agent
        return "cursor"
    return agent


_MAESTRO_CSE_WAKE_TARGETS = frozenset({"maestro", "web-anthropic"})


def _maestro_cse_nonwake_head(
    *, to_agent: str, from_agent: str, subject: str, head: str
) -> bool:
    """L17/L19: house telemetry and intermediate subjects to maestro/CSE do not wake."""
    if to_agent not in _MAESTRO_CSE_WAKE_TARGETS:
        return False
    _ = _MAESTRO_MILESTONE_WAKE_HEADS
    token = head.split()[0] if head else ""
    if token in {"FACT", "PICKUP", "UPDATE", "HOP"}:
        return True
    if head == "STATUS" or token == "STATUS":
        parts = [p.strip().lower() for p in (subject or "").split("|")]
        if (
            len(parts) >= 2
            and parts[0] == "status"
            and parts[1] == "ack-batch"
            and to_agent == "web-anthropic"
            and from_agent == "grok-bot-gc1"
        ):
            return False
        return True
    text = subject or ""
    if text.strip().lower().startswith("score_resurface"):
        return True
    if _INTERMEDIATE_SUBJECT_RE.search(text):
        checkpoint_head = text.split("|", 1)[0].strip().upper()
        if not checkpoint_head.startswith("CHECKPOINT"):
            return True
    lowered = text.lower()
    for marker in ("closeout", "cursor-sdk generate", "cdp generate"):
        if marker in lowered:
            return True
    return False


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
    lowered = text.lower()
    if (
        "contract=conductor" in lowered
        and "caller=conductor-hop" in lowered
        and "degraded" not in lowered
    ):
        return False
    agent = _effective_to_agent(to_agent, text)
    if _INTERMEDIATE_SUBJECT_RE.search(text):
        if agent == "cursor" and _CURSOR_GATE_WAKE_RE.search(text):
            return True
        return False
    if _CURSOR_GATE_WAKE_RE.search(text) and (agent == "cursor" or not cse):
        return True
    if text.startswith("TYPE: DIRECTIVE"):
        return True
    if not cse and text.startswith(_ROUTE_CONTROL_TYPE):
        return True
    allowed = CSE_WAKE_SUBJECT_HEADS if cse else GROK_WAKE_SUBJECT_HEADS
    head = text.split("|", 1)[0].strip().upper()
    if not cse and head == "NOTICE":
        parts = [p.strip().lower() for p in text.split("|")]
        return len(parts) >= 2 and parts[1] == "gap"
    if head in allowed:
        return True
    if "|" in text and head:
        first_token = head.split()[0]
        if first_token in allowed:
            return True
    if (
        not cse
        and head
        and head.split()[0]
        in {
            "DONE",
            "FAILED",
            "BLOCKED",
            "QUESTION",
        }
    ):
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
    if _maestro_cse_nonwake_head(
        to_agent=to_agent, from_agent=from_agent, subject=subject, head=head
    ):
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


def _display_seated(store: StoreClient, thread: str) -> bool:
    """True when ``thread`` has a non-CSE (display) cursor route; it then owns the sole wake path."""
    resolved = _resolve_delivery_route(store, "cursor", thread)
    return resolved is not None and str(resolved[1].get("surface") or "") != "cse"


def scan_once(store: StoreClient, *, idle_mark: dict[str, float] | None = None) -> None:
    """Read new turns and CAS pending rows. Store and GIW errors propagate."""
    import time

    expire_overdue_hops(store)
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
            if agent == "cursor":
                try:
                    expire_overdue_hop(thread, store=store)
                except OSError:
                    logger.exception(
                        "ide_hop_land: expire overdue hop failed root=%s",
                        thread,
                    )
            resolved = _resolve_delivery_route(store, agent, thread, raw_to=raw_to)
            cse = (
                resolved is not None and str(resolved[1].get("surface") or "") == "cse"
            )
        if not qualifies(turn, cse=cse):
            continue
        if agent == "web-anthropic":
            if _display_seated(store, thread) or not store.holder(thread):
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
    row: dict[str, Any],
    sub: dict[str, Any],
    turn: int | str,
    *,
    text: str = "",
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
    if (text or "").strip():
        options["text"] = text.strip()
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


_MAESTRO_SEAT_REGISTRATION_THREAD = "12286"
_SEAT_REGISTRATION_HEAD = "TYPE: SEAT_REGISTRATION"
_WAKE_REGISTRATION_REFUSED_SUBJECT = "FACT|wake-registration-refused"
_WAKE_REGISTRATION_REFUSED_TARGETS = ("maestro", "grok-bot-gc1")


@dataclass(frozen=True)
class SeatRegistrationSnapshot:
    """Merged tab-bind stamp plus optional seat-side Act 6 turn on the hop lane."""

    registration_id: str
    chat_url: str
    bind_successor_birth_id: str | None = None
    seat_successor_birth_id: str | None = None
    seat_chat_url: str | None = None


def _seat_registration_header_fields(body: str) -> dict[str, str]:
    """Column-0 header keys only — indented quoted lines must not impersonate stamps."""
    text = str(body or "")
    lines = text.splitlines()
    if not lines or not lines[0].strip().startswith(_SEAT_REGISTRATION_HEAD):
        return {}
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.startswith("registration_id:"):
            fields["registration_id"] = line.split(":", 1)[1].strip()
        elif line.startswith("chat_url:"):
            fields["chat_url"] = line.split(":", 1)[1].strip()
        elif line.startswith("successor_birth_id:"):
            fields["successor_birth_id"] = line.split(":", 1)[1].strip()
        elif line.startswith("source: cursor-auto"):
            fields["source_cursor_auto"] = "1"
    return fields


def parse_seat_registration_stamp(body: str) -> tuple[str, str] | None:
    """Return ``(registration_id, chat_url)`` for a tab-bind ``SEAT_REGISTRATION`` stamp."""
    fields = _seat_registration_header_fields(body)
    registration_id = fields.get("registration_id", "")
    chat_url = fields.get("chat_url", "")
    if registration_id and chat_url:
        return registration_id, chat_url
    return None


def _parse_bind_seat_registration(
    body: str,
) -> tuple[str, str, str | None, bool] | None:
    """Tab-bind stamp: column-0 ``registration_id`` and ``chat_url`` (K63 / B1)."""
    fields = _seat_registration_header_fields(body)
    registration_id = fields.get("registration_id", "")
    chat_url = fields.get("chat_url", "")
    if registration_id and chat_url:
        birth = fields.get("successor_birth_id") or None
        cursor_auto = bool(fields.get("source_cursor_auto"))
        return registration_id, chat_url, birth, cursor_auto
    return None


def _parse_seat_side_registration(
    body: str,
) -> tuple[str, str | None] | None:
    """Successor Act 6 stamp: ``successor_birth_id`` without ``registration_id``."""
    fields = _seat_registration_header_fields(body)
    if fields.get("registration_id"):
        return None
    birth = fields.get("successor_birth_id", "")
    if not birth:
        return None
    seat_url = fields.get("chat_url") or None
    return birth, seat_url


_SEAT_REGISTRATION_SCAN_PAGE = 500


def load_latest_seat_registration_stamp(
    store: StoreClient, *, thread: str = _MAESTRO_SEAT_REGISTRATION_THREAD
) -> SeatRegistrationSnapshot | None:
    """Load bind + seat-side ``SEAT_REGISTRATION`` state on ``thread`` by ``turn_number``.

    Walks the full thread in ascending pages (not the tip ``last=100`` window) so an
    authoritative stamp is not dropped when more than 100 newer non-registration turns
    follow it (specimen 12286#4490 vs #4482). The tab bind supplies ``registration_id``
    and ``chat_url``; a later seat Act 6 turn may add ``successor_birth_id`` only.
    """
    bind_turn = -1
    bind: tuple[str, str, str | None] | None = None
    seat_birth: str | None = None
    seat_chat_url: str | None = None
    seat_turn = -1
    seen_cursor_auto_bind = False
    after_turn = 0
    while True:
        payload = store.turns(
            thread, after_turn=after_turn, last=_SEAT_REGISTRATION_SCAN_PAGE
        )
        turns = list(payload.get("turns") or [])
        if not turns:
            break
        for row in turns:
            try:
                turn_number = int(row.get("turn_number") or 0)
            except (TypeError, ValueError):
                continue
            body = str(row.get("body") or "")
            from_agent = str(row.get("from") or row.get("from_agent") or "")
            bind_parsed = _parse_bind_seat_registration(body)
            if bind_parsed is not None:
                reg, url, birth, cursor_auto = bind_parsed
                machine_bind = cursor_auto and from_agent == "cursor-auto"
                if machine_bind:
                    seen_cursor_auto_bind = True
                if seen_cursor_auto_bind and not machine_bind:
                    continue
                if turn_number > bind_turn:
                    bind_turn = turn_number
                    bind = (reg, url, birth)
                seat_birth = None
                seat_chat_url = None
                seat_turn = -1
                continue
            seat_parsed = _parse_seat_side_registration(body)
            if (
                bind is not None
                and seat_parsed is not None
                and turn_number > bind_turn
                and turn_number > seat_turn
            ):
                seat_turn = turn_number
                seat_birth, seat_chat_url = seat_parsed
        after_turn = int(turns[-1]["turn_number"])
        if not payload.get("truncated"):
            break
    if bind is None:
        return None
    reg, url, bind_birth = bind
    return SeatRegistrationSnapshot(
        registration_id=reg,
        chat_url=url,
        bind_successor_birth_id=bind_birth,
        seat_successor_birth_id=seat_birth,
        seat_chat_url=seat_chat_url,
    )


def holder_matches_seat_registration(
    holder: dict[str, Any], stamp: SeatRegistrationSnapshot | tuple[str, str] | None
) -> bool:
    """True when holder agrees with the bind stamp and seat birth ids do not conflict."""
    if stamp is None:
        return False
    if isinstance(stamp, tuple):
        snapshot = SeatRegistrationSnapshot(
            registration_id=stamp[0],
            chat_url=stamp[1],
        )
    else:
        snapshot = stamp
    bind_birth = snapshot.bind_successor_birth_id
    seat_birth = snapshot.seat_successor_birth_id
    if bind_birth and seat_birth and bind_birth != seat_birth:
        return False
    if snapshot.seat_chat_url:
        seat_hid = holder_id_from_chat_url(snapshot.seat_chat_url)
        bind_hid = holder_id_from_chat_url(snapshot.chat_url)
        if seat_hid and bind_hid and seat_hid != bind_hid:
            return False
    stamp_reg = snapshot.registration_id
    stamp_url = snapshot.chat_url
    holder_reg = str(holder.get("registration_id") or "").strip()
    if holder_reg != stamp_reg:
        return False
    holder_url = str(holder.get("chat_url") or "").strip()
    holder_hid = holder_id_from_chat_url(holder_url)
    stamp_hid = holder_id_from_chat_url(stamp_url)
    if holder_hid and stamp_hid:
        return holder_hid == stamp_hid
    return holder_url == stamp_url


def build_wake_registration_refused_body(
    *,
    rejected_registration_id: str,
    rejected_chat_url: str,
    stamp_registration_id: str,
    stamp_chat_url: str,
) -> str:
    """Body for ``FACT|wake-registration-refused`` alerts on the operator lane."""
    return "\n".join(
        [
            "wake-registration-refused",
            f"rejected_registration_id: {rejected_registration_id}",
            f"rejected_chat_url: {rejected_chat_url}",
            f"stamp_registration_id: {stamp_registration_id}",
            f"stamp_chat_url: {stamp_chat_url}",
        ]
    )


def post_wake_registration_refused_alerts(
    store: StoreClient,
    *,
    rejected_registration_id: str,
    rejected_chat_url: str,
    stamp_registration_id: str,
    stamp_chat_url: str,
    thread: str = _MAESTRO_SEAT_REGISTRATION_THREAD,
) -> None:
    """Post FACT alerts to maestro and the grok gc1 window slot (not a mailbox)."""
    body = build_wake_registration_refused_body(
        rejected_registration_id=rejected_registration_id,
        rejected_chat_url=rejected_chat_url,
        stamp_registration_id=stamp_registration_id,
        stamp_chat_url=stamp_chat_url,
    )
    poster = getattr(store, "create_turn", None)
    if not callable(poster):
        logger.warning(
            "endpoint_wake_dispatcher: create_turn unavailable; "
            "skipped FACT|wake-registration-refused alerts thread=%s",
            thread,
        )
        return
    for to_agent in _WAKE_REGISTRATION_REFUSED_TARGETS:
        try:
            poster(
                thread=thread,
                from_agent="agent_bus",
                to_agent=to_agent,
                subject=_WAKE_REGISTRATION_REFUSED_SUBJECT,
                body=body,
            )
        except Exception:
            logger.exception(
                "endpoint_wake_dispatcher: wake-registration-refused alert failed "
                "thread=%s to=%s",
                thread,
                to_agent,
            )


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


def _drop_dead_agent_subscription(
    store: StoreClient,
    *,
    agent: str,
    thread: str,
    sub: dict[str, Any],
    reason: str,
) -> None:
    """Unsubscribe a Cursor route whose subscribed ``agent_id`` has no focusable composer."""
    if str(sub.get("surface") or "") != "cursor":
        return
    dead_agent_id = str(sub.get("agent_id") or "").strip()
    scope = str(sub.get("thread_scope") or thread).strip()
    if not dead_agent_id or scope in {"", "*"}:
        return
    sub_agent = str(sub.get("agent") or agent).strip() or "cursor"
    evidence = f"{reason}:{dead_agent_id}"
    unsub = getattr(store, "unsubscribe", None)
    if callable(unsub):
        try:
            unsub(
                sub_agent,
                scope,
                bound_by="endpoint-wake-dispatcher",
                evidence=evidence,
                expected_agent_id=dead_agent_id,
            )
        except Exception:
            logger.exception(
                "endpoint_wake_dispatcher: unsubscribe failed agent=%s scope=%s",
                sub_agent,
                scope,
            )
    if os.environ.get("AGENT_BUS_DB_PATH"):
        try:
            from agent_bus_store.db.endpoint_subscriptions import (
                current_subscription,
                unsubscribe_endpoint,
            )

            cur = current_subscription(sub_agent, scope)
            if (
                cur is not None
                and cur.get("state") == "subscribed"
                and str(cur.get("agent_id") or "").strip() == dead_agent_id
            ):
                unsubscribe_endpoint(
                    agent=sub_agent,
                    thread_scope=scope,
                    bound_by="endpoint-wake-dispatcher",
                    evidence=evidence,
                    expected_agent_id=dead_agent_id,
                )
        except Exception:
            logger.exception(
                "endpoint_wake_dispatcher: local unsubscribe failed agent=%s scope=%s",
                sub_agent,
                scope,
            )


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
        if _display_seated(store, thread):
            store.result(
                wake_id=wake_id,
                outcome="skipped",
                reason="display_seated",
                route=None,
                subscription_id=None,
                cse_registration_id=None,
            )
            return claimed
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
        maestro_stamp: SeatRegistrationSnapshot | None = None
        if agent == "maestro":
            maestro_stamp = load_latest_seat_registration_stamp(store)
            if not holder_matches_seat_registration(holder, maestro_stamp):
                stamp_reg = maestro_stamp.registration_id if maestro_stamp else ""
                stamp_url = maestro_stamp.chat_url if maestro_stamp else ""
                store.result(
                    wake_id=wake_id,
                    outcome="skipped",
                    reason="seat_registration_mismatch",
                    route="cse",
                    subscription_id=sub.get("id"),
                    cse_registration_id=None,
                )
                post_wake_registration_refused_alerts(
                    store,
                    rejected_registration_id=str(holder.get("registration_id") or ""),
                    rejected_chat_url=str(holder.get("chat_url") or ""),
                    stamp_registration_id=stamp_reg,
                    stamp_chat_url=stamp_url,
                )
                return claimed
        state, reason, reg = _deliver_cse(rows, holder, target_lane=lane)
        stamp_reg = maestro_stamp.registration_id if maestro_stamp else ""
        stamp_url = maestro_stamp.chat_url if maestro_stamp else ""
        post_paste_mismatch = (
            agent == "maestro"
            and state == "delivered"
            and reg
            and stamp_reg
            and reg != stamp_reg
        )
        if post_paste_mismatch:
            state = "refused"
            reason = "seat_registration_mismatch_post_paste"
        need_refused_alert = agent == "maestro" and (
            reason.startswith("cse_authority_split") or post_paste_mismatch
        )
        store.result(
            wake_id=wake_id,
            outcome=state,
            reason=reason,
            route="cse",
            subscription_id=sub.get("id"),
            cse_registration_id=reg,
        )
        if need_refused_alert:
            post_wake_registration_refused_alerts(
                store,
                rejected_registration_id=str(
                    reg or holder.get("registration_id") or ""
                ),
                rejected_chat_url=str(holder.get("chat_url") or ""),
                stamp_registration_id=stamp_reg,
                stamp_chat_url=stamp_url,
            )
        return claimed
    turn_numbers = sorted({int(r["turn_number"]) for r in rows})
    turn_arg: int | str = (
        turn_numbers[0]
        if len(turn_numbers) == 1
        else ",".join(str(n) for n in turn_numbers)
    )
    paste_text = ""
    if (
        str(rows[0]["thread_id"]) == "19627"
        and str(sub.get("surface") or "") == "cursor"
    ):
        paste_text = exchange_lines(rows)
    state, reason = _display_post(rows[0], sub, turn_arg, text=paste_text)
    display_reachable_age = _note_display_post_reason(reason)
    if _update_no_composer_streak_and_gate_unsubscribe(
        sub, thread, reason, display_reachable_age=display_reachable_age
    ):
        _drop_dead_agent_subscription(
            store, agent=agent, thread=thread, sub=sub, reason=reason
        )
        hop_root = str(sub.get("thread_scope") or thread)
        try:
            expire_overdue_hop(hop_root, store=store)
        except OSError:
            logger.exception(
                "ide_hop_land: expire overdue hop failed root=%s",
                hop_root,
            )
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
            await asyncio.to_thread(expire_overdue_hops, store)
            await asyncio.to_thread(scan_once, store, idle_mark=idle)
            for _ in range(5):
                claimed = await asyncio.to_thread(fire_due_wakes, store)
                if not isinstance(claimed, dict) or not claimed.get("wake_id"):
                    break
            await asyncio.to_thread(check_cursor_ide_context_hops, store, mark=hop_mark)
            from bus_watch.impresario_ear_patrol import run_patrol_pass

            await asyncio.to_thread(run_patrol_pass, store)
        except Exception:
            logger.exception("endpoint_wake_dispatcher pass failed")
        await asyncio.sleep(_POLL_S)
