"""1-to-1 Electron chat paste-and-read relay over CDP (gc1 <-> gc2 or cursor <-> grok).

Uses the shared ``cursor_bridge`` Electron window and sidebar agent selection
interface (`list_pages`, `probe_surface`, `select_sidebar_target`) to:
1. Select the bound agent row in a target Electron window (`:8`, `:9`, `:10`)
   when the currently active composer is empty.
2. Read the latest settled turn from `.sand-message-prose` once `generating` is
   `False`.
3. Paste any literal message text into a peer Electron window's selected agent
   composer and submit it, repeating 1-to-1 until the conversation moves to
   `agent_bus` + wakes (`HANDOFF_TO_BUS` or `BRIDGE_WAKE`).
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any
from urllib.error import URLError

from cursor_bridge.cdp_pages import list_pages
from cursor_bridge.chat_surface import (
    probe_surface,
    select_sidebar_target,
    wake_decision,
)
from cursor_bridge.wake_delivery import (
    _COMPOSER_JS,
    _busy_or_draft_reason,
    _display_lock,
    _paste,
    _pick_page,
    _refuse,
    normalize_target,
)

HANDOFF_MARKERS = ("HANDOFF_TO_BUS", "BRIDGE_WAKE", "AGENT_BUS_READY")


@dataclass(frozen=True, slots=True)
class ElectronEndpoint:
    """Identifies one sidebar agent inside an X11 Electron window."""

    label: str
    app: str
    display: str
    title: str = ""
    agent_id: str = ""
    thread: str = ""
    slug: str = ""


def message_fingerprint(text: str) -> str:
    """Stable SHA-256 hex digest of normalized message prose."""
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


def should_stop_for_bus_handoff(
    text: str, *, extra_markers: tuple[str, ...] = ()
) -> bool:
    """True when the turn text signals transition from paste relay to agent_bus."""
    body = (text or "").strip()
    if not body:
        return False
    markers = HANDOFF_MARKERS + tuple(m for m in extra_markers if m)
    return any(marker in body for marker in markers)


async def _resolve_and_select_endpoint(
    endpoint: ElectronEndpoint, *, select: bool
) -> dict[str, Any]:
    """Open CDP page, probe surface, and select ``endpoint`` if needed."""
    target = normalize_target(endpoint.display, endpoint.app)
    if not target.get("ok"):
        return {
            "ok": False,
            "reason": "unknown_target",
            "endpoint": endpoint.label,
            "display": target.get("display"),
            "app": target.get("app"),
        }
    disp, app = target["display"], target["app"]
    try:
        pages = list_pages(target["origin"])
    except URLError as exc:
        return {"ok": False, "reason": "debug_unreachable", "endpoint": endpoint.label, "display": disp, "app": app, "detail": type(exc).__name__}
    page = _pick_page(pages, app)
    if page is None:
        return {"ok": False, "reason": "no_page", "endpoint": endpoint.label, "display": disp, "app": app, "pages": len(pages)}
    ws_url = page["web_socket_debugger_url"]
    try:
        surface = await probe_surface(ws_url)
    except (TimeoutError, RuntimeError, OSError) as exc:
        return {"ok": False, "reason": "surface_probe_failed", "endpoint": endpoint.label, "display": disp, "app": app, "detail": type(exc).__name__}
    verdict = wake_decision(
        surface,
        thread=endpoint.thread,
        slug=endpoint.slug,
        title=endpoint.title or None,
        agent_id=endpoint.agent_id or None,
    )
    selected = False
    if not verdict.get("ok") and select and verdict.get("state") == "chat_list":
        guard = _busy_or_draft_reason(surface)
        if guard:
            return {"ok": False, "reason": guard, "endpoint": endpoint.label, "display": disp, "app": app, "active_title": verdict.get("active_title")}
        click_res = await select_sidebar_target(
            ws_url,
            expected_title=str(verdict.get("expected_title") or ""),
            expected_agent_id=str(verdict.get("expected_agent_id") or ""),
        )
        if click_res.get("clicked"):
            selected = True
            await asyncio.sleep(0.25)
            surface = await probe_surface(ws_url)
            verdict = wake_decision(
                surface,
                thread=endpoint.thread,
                slug=endpoint.slug,
                title=endpoint.title or None,
                agent_id=endpoint.agent_id or None,
            )
    if not verdict.get("ok"):
        return {**verdict, "ok": False, "endpoint": endpoint.label, "display": disp, "app": app, "selected": selected}
    return {
        "ok": True,
        "ws_url": ws_url,
        "surface": surface,
        "verdict": verdict,
        "selected": selected,
        "display": disp,
        "app": app,
    }


async def read_endpoint_turn(
    endpoint: ElectronEndpoint, *, select: bool = True
) -> dict[str, Any]:
    """Select ``endpoint`` (if needed and safe) and return its latest prose state."""
    target = normalize_target(endpoint.display, endpoint.app)
    disp = str(target.get("display") or endpoint.display)
    async with _display_lock(disp):
        res = await _resolve_and_select_endpoint(endpoint, select=select)
        if not res.get("ok"):
            return res
        surface, verdict = res["surface"], res["verdict"]
        messages = [str(m).strip() for m in (surface.get("recent_messages") or []) if str(m).strip()]
        latest = messages[-1] if messages else ""
        return {
            "ok": True,
            "endpoint": endpoint.label,
            "display": res["display"],
            "app": res["app"],
            "selected": res["selected"],
            "active_title": verdict.get("active_title"),
            "active_agent_id": verdict.get("active_agent_id"),
            "generating": bool(surface.get("generating")),
            "composer_draft": str(surface.get("composer_draft") or ""),
            "recent_messages": messages,
            "latest_message": latest,
            "latest_fingerprint": message_fingerprint(latest) if latest else "",
        }


async def paste_endpoint_message(
    endpoint: ElectronEndpoint, message: str, *, select: bool = True
) -> dict[str, Any]:
    """Select ``endpoint`` and paste + submit arbitrary ``message`` text."""
    text = (message or "").strip()
    if not text:
        return _refuse("empty_message", endpoint=endpoint.label)
    target = normalize_target(endpoint.display, endpoint.app)
    disp = str(target.get("display") or endpoint.display)
    async with _display_lock(disp):
        res = await _resolve_and_select_endpoint(endpoint, select=select)
        if not res.get("ok"):
            return {**res, "pasted": False, "delivered": False}
        ws_url, surface, verdict = res["ws_url"], res["surface"], res["verdict"]
        disp, app, selected = res["display"], res["app"], res["selected"]
        guard = _busy_or_draft_reason(surface)
        if guard:
            return _refuse(guard, endpoint=endpoint.label, display=disp, app=app, selected=selected)
        composer = _COMPOSER_JS.get(app)
        if composer is None:
            return _refuse("composer_unconfirmed", endpoint=endpoint.label, display=disp, app=app)
        flat_line = " ".join(text.splitlines()).strip()
        try:
            paste_res = await _paste(ws_url, composer, flat_line)
        except (TimeoutError, RuntimeError, OSError) as exc:
            return _refuse("paste_failed", endpoint=endpoint.label, display=disp, app=app, detail=type(exc).__name__)
        if not paste_res.get("ok"):
            return _refuse(str(paste_res.get("reason") or "no_composer"), endpoint=endpoint.label, display=disp, app=app)
        await asyncio.sleep(0.25)
        post = await probe_surface(ws_url)
        delivered = bool(
            not str(post.get("composer_draft") or "").strip()
            or any(flat_line in msg for msg in (post.get("recent_messages") or []))
        )
        return {
            **verdict,
            "ok": delivered,
            "reason": verdict.get("reason", "match") if delivered else "delivery_unconfirmed",
            "pasted": True,
            "delivered": delivered,
            "selected": selected,
            "endpoint": endpoint.label,
            "display": disp,
            "app": app,
            "message": flat_line,
            "fingerprint": message_fingerprint(flat_line),
        }


async def wait_for_new_settled_turn(
    endpoint: ElectronEndpoint,
    *,
    baseline_fingerprint: str,
    poll_interval_s: float = 2.0,
    timeout_s: float = 180.0,
) -> dict[str, Any]:
    """Poll ``endpoint`` until ``generating`` is False and a new turn appears."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        snap = await read_endpoint_turn(endpoint, select=True)
        if not snap.get("ok"):
            return snap
        fp = str(snap.get("latest_fingerprint") or "")
        if not snap.get("generating") and fp and fp != baseline_fingerprint:
            return snap
        await asyncio.sleep(poll_interval_s)
    return {"ok": False, "reason": "turn_wait_timeout", "endpoint": endpoint.label, "baseline_fingerprint": baseline_fingerprint}


async def run_one_to_one_relay(
    left: ElectronEndpoint,
    right: ElectronEndpoint,
    *,
    seed_message: str | None = None,
    max_exchanges: int = 10,
    poll_interval_s: float = 2.0,
    turn_timeout_s: float = 180.0,
    extra_stop_markers: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Repeatable 1-to-1 copy-and-paste relay between ``left`` (gc1) and ``right`` (gc2)."""
    transcript: list[dict[str, Any]] = []
    left_snap = await read_endpoint_turn(left, select=True)
    if not left_snap.get("ok"):
        return {"ok": False, "stage": "init_left", "detail": left_snap, "transcript": transcript}
    right_snap = await read_endpoint_turn(right, select=True)
    if not right_snap.get("ok"):
        return {"ok": False, "stage": "init_right", "detail": right_snap, "transcript": transcript}

    last_fp: dict[str, str] = {
        left.label: str(left_snap.get("latest_fingerprint") or ""),
        right.label: str(right_snap.get("latest_fingerprint") or ""),
    }
    speaker, listener = left, right
    if seed_message and seed_message.strip():
        pasted = await paste_endpoint_message(left, seed_message.strip(), select=True)
        if not pasted.get("ok"):
            return {"ok": False, "stage": "seed_paste", "detail": pasted, "transcript": transcript}
        last_fp[left.label] = str(pasted.get("fingerprint") or "")
        transcript.append({"step": 0, "from": "seed", "to": left.label, "message": pasted.get("message")})

    for step in range(1, max_exchanges + 1):
        turn = await wait_for_new_settled_turn(
            speaker,
            baseline_fingerprint=last_fp[speaker.label],
            poll_interval_s=poll_interval_s,
            timeout_s=turn_timeout_s,
        )
        if not turn.get("ok"):
            return {"ok": False, "stage": f"wait_turn_{step}_{speaker.label}", "detail": turn, "transcript": transcript}
        reply_text = str(turn.get("latest_message") or "").strip()
        last_fp[speaker.label] = str(turn.get("latest_fingerprint") or "")
        if should_stop_for_bus_handoff(reply_text, extra_markers=extra_stop_markers):
            transcript.append({"step": step, "from": speaker.label, "to": "agent_bus", "message": reply_text, "handoff": True})
            return {"ok": True, "stopped_reason": "bus_handoff", "step": step, "speaker": speaker.label, "transcript": transcript}
        listener_snap = await read_endpoint_turn(listener, select=True)
        if listener_snap.get("ok"):
            last_fp[listener.label] = str(listener_snap.get("latest_fingerprint") or "")
        pasted = await paste_endpoint_message(listener, reply_text, select=True)
        if not pasted.get("ok"):
            return {"ok": False, "stage": f"paste_step_{step}_{listener.label}", "detail": pasted, "transcript": transcript}
        last_fp[listener.label] = str(pasted.get("fingerprint") or "")
        transcript.append({"step": step, "from": speaker.label, "to": listener.label, "message": reply_text})
        speaker, listener = listener, speaker

    return {"ok": True, "stopped_reason": "max_exchanges_reached", "step": max_exchanges, "transcript": transcript}


__all__ = [
    "ElectronEndpoint",
    "HANDOFF_MARKERS",
    "message_fingerprint",
    "paste_endpoint_message",
    "read_endpoint_turn",
    "run_one_to_one_relay",
    "should_stop_for_bus_handoff",
    "wait_for_new_settled_turn",
]
