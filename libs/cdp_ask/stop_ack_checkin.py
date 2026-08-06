"""STOP-ACK stream-stop check-in — mission CSE backup liveness timer."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from claude_bundles.operator_proxy_mission import is_operator_proxy_mission_purpose

from cdp_ask.execution_store import ExecutionRecord, ExecutionStore
from cdp_ask.models import FollowupProjectAskRequest
from cdp_ask.stop_ack_events import (
    cdp_ask_stop_ack_ack,
    cdp_ask_stop_ack_checkin_attempt,
    cdp_ask_stop_ack_no_ack,
)
from cdp_ask.stop_ack_events import emit as emit_stop_ack_event

STOP_ACK_QUIET_S = 90.0

_STOP_ACK_PROMPT = """\
Stream-stop check-in (BINDING). Reply with exactly one line:

STOP-ACK intentional
STOP-ACK unintentional
STOP-ACK parked <job>
"""

_PARKED_RE = re.compile(r"^STOP-ACK parked\s+(\S+)$")


class StopAckRoute(StrEnum):
    INTENTIONAL = "intentional"
    UNINTENTIONAL = "unintentional"
    PARKED = "parked"


@dataclass(frozen=True)
class ParsedStopAck:
    route: StopAckRoute
    job: str | None = None


def is_stop_ack_candidate(rec: ExecutionRecord, now: float) -> bool:
    """True when *rec* matches the F1 check-in candidate predicate."""
    if rec.status not in {"pending", "running"}:
        return False
    if not is_operator_proxy_mission_purpose(rec.purpose):
        return False
    if rec.completion_phase != "running":
        return False
    if not (rec.stop is True or rec.streaming is False):
        return False
    if rec.liveness_observed_at is None:
        return False
    return (now - rec.liveness_observed_at) > STOP_ACK_QUIET_S


def parse_stop_ack(body: str) -> ParsedStopAck | None:
    """Parse scraped model reply for STOP-ACK tokens; unparseable body is not ACK."""
    text = (body or "").strip()
    if not text:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "STOP-ACK intentional":
            return ParsedStopAck(route=StopAckRoute.INTENTIONAL)
        if stripped == "STOP-ACK unintentional":
            return ParsedStopAck(route=StopAckRoute.UNINTENTIONAL)
        parked = _PARKED_RE.match(stripped)
        if parked:
            return ParsedStopAck(route=StopAckRoute.PARKED, job=parked.group(1))
    return None


def build_stop_ack_prompt() -> str:
    """Scrapable check-in prompt containing the three ACK tokens."""
    return _STOP_ACK_PROMPT


async def _default_execute_followup(
    req: FollowupProjectAskRequest, store: ExecutionStore
) -> Any:
    from cdp_ask.followup import execute_followup

    return await execute_followup(req, store)


async def _harvest_reply_body(
    registration_id: str, chat_url: str | None
) -> str | None:
    """Best-effort assistant reply scrape after paste (short idle wait)."""
    from claude_bundles import cdp_registry
    from claude_bundles.chat_reply_wait import wait_assistant_reply

    from cdp_ask.followup import _find_page_on_lane

    reg = None
    for candidate in cdp_registry.list_active():
        if candidate.registration_id == registration_id:
            reg = candidate
            break
    if reg is None:
        return None
    url = (chat_url or reg.chat_url or "").strip()
    if not url:
        return None
    found = await _find_page_on_lane(reg.cdp_url, url)
    if found is None:
        return None
    page, pw = found
    try:
        state = await wait_assistant_reply(
            page,
            timeout_s=30,
            poll_ms=500,
            min_body=1,
            min_growth=1,
            stable_polls=1,
        )
        body = str(state.get("body") or state.get("last_body") or "")
        return body.strip() or None
    except Exception:
        return None
    finally:
        await pw.stop()


async def _attempt_checkin_paste(
    *,
    rec: ExecutionRecord,
    store: ExecutionStore,
    execute_followup_fn: Callable[..., Any],
) -> tuple[str, str | None, bool]:
    """Paste check-in on attached lane; returns (route, reply_body, lane_created)."""
    from cdp_ask.followup_resolve import resolve_followup_target

    req = FollowupProjectAskRequest(
        execution_id=rec.execution_id,
        registration_id=rec.registration_id,
        purpose=rec.purpose,
        prompt_text=build_stop_ack_prompt(),
        reattach=False,
        min_receipt="dom_paste",
    )
    target, err, _path = await resolve_followup_target(req, store)
    if err is not None or target is None:
        return "bus_wake+pager", None, False

    resp = await execute_followup_fn(req, store)
    lane_created = bool(getattr(resp, "lane_created", False))
    if not resp.ok or lane_created:
        if lane_created:
            return "bus_wake+pager", None, True
        return "bus_wake+pager", None, False

    body = await _harvest_reply_body(
        target.registration_id,
        getattr(resp, "url", None) or target.chat_url,
    )
    return "paste", body, False


async def _route_bus_wake_pager(
    *,
    execution_id: str,
    registration_id: str | None,
    thread: str | None,
    purpose: str | None,
    notify_pager: Callable[[str, str], bool] | None,
) -> None:
    """Degrade to bus wake + pager when identity is unresolvable (I3)."""
    subject = f"STOP-ACK check-in identity miss execution={execution_id}"
    body = (
        f"execution_id={execution_id} registration_id={registration_id or ''} "
        f"thread={thread or ''} purpose={purpose or ''} route=bus_wake+pager"
    )
    if notify_pager:
        notify_pager(subject, body)


async def run_checkin_tick(
    store: ExecutionStore,
    *,
    now: float | None = None,
    execute_followup_fn: Callable[..., Any] | None = None,
    notify_pager: Callable[[str, str], bool] | None = None,
) -> list[dict[str, Any]]:
    """One STOP-ACK timer tick: candidacy scan, paste/ACK routing, TTL sweep."""
    from claude_bundles.cse_session_obligations import (
        discharge_stop_ack_owed,
        get_open_stop_ack_owed_for_execution,
        mint_stop_ack_owed,
        sweep_stop_ack_owed_ttl,
    )

    ts = now if now is not None else time.time()
    followup_fn = execute_followup_fn or _default_execute_followup
    results: list[dict[str, Any]] = []

    candidates = await store.iter_stop_ack_candidates(ts)
    for rec in candidates:
        ob = get_open_stop_ack_owed_for_execution(rec.execution_id)
        if ob is None:
            mint_stop_ack_owed(
                execution_id=rec.execution_id,
                registration_id=rec.registration_id,
                purpose=rec.purpose,
                now=ts,
            )

        route, reply_body, lane_created = await _attempt_checkin_paste(
            rec=rec,
            store=store,
            execute_followup_fn=followup_fn,
        )
        emit_stop_ack_event(
            cdp_ask_stop_ack_checkin_attempt(
                execution_id=rec.execution_id,
                registration_id=rec.registration_id,
                purpose=rec.purpose,
                route=route,  # type: ignore[arg-type]
                lane_created=lane_created,
            )
        )

        if route == "bus_wake+pager" or lane_created:
            await _route_bus_wake_pager(
                execution_id=rec.execution_id,
                registration_id=rec.registration_id,
                thread=None,
                purpose=rec.purpose,
                notify_pager=notify_pager,
            )
            results.append(
                {
                    "execution_id": rec.execution_id,
                    "route": "bus_wake+pager",
                    "lane_created": lane_created,
                }
            )
            continue

        parsed = parse_stop_ack(reply_body or "")
        if parsed is None:
            results.append({"execution_id": rec.execution_id, "ack": None})
            continue

        ack_route = parsed.route.value
        emit_stop_ack_event(
            cdp_ask_stop_ack_ack(
                execution_id=rec.execution_id,
                ack=ack_route,  # type: ignore[arg-type]
                job=parsed.job,
            )
        )
        discharge_stop_ack_owed(
            execution_id=rec.execution_id,
            reason=parsed.route.value,
            job=parsed.job,
        )
        results.append({"execution_id": rec.execution_id, "ack": ack_route})

    for alarm in sweep_stop_ack_owed_ttl(now=ts, notify_pager=notify_pager):
        exec_id = str(alarm.get("execution_id") or "")
        emit_stop_ack_event(
            cdp_ask_stop_ack_no_ack(
                execution_id=exec_id,
                registration_id=alarm.get("registration_id"),
            )
        )
        results.append({"execution_id": exec_id, "alarm": True})

    return results
