"""Deliver one display wake, or refuse it without raising.

Callers are the ``display-chat-wake`` pipeline step. The debug port is
chosen from ``app`` and ``display``; the sidebar observation comes from
``probe_surface``. When ``select=True`` (default) and the target row is
present in the sidebar, the step can switch to that row provided the
currently active chat's composer is empty (streaming/generating does not
block steering or row selection). A match pastes
``BRIDGE_WAKE thread=<id> turn=<n>`` into that app's composer, submits it,
and verifies delivery. Any other outcome returns ``ok`` false. This module
does not fail a pipeline run.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from urllib.error import URLError

from cursor_bridge.cdp_pages import list_pages
from cursor_bridge.chat_surface import (
    probe_surface,
    select_sidebar_target,
    wake_decision,
)
from cursor_bridge.chat_target import wake_line

_TARGETS: dict[tuple[str, str], str] = {
    ("cursor", "8"): "http://127.0.0.1:9338",
    ("cursor", "orion-node"): "http://127.0.0.1:19338",
    ("grok", "9"): "http://127.0.0.1:9339",
    ("grok", "10"): "http://127.0.0.1:9340",
}
_WINDOW_TITLE = {"cursor": "Cursor Agents", "grok": "Grok Bot"}
_DISPLAY_LOCKS: dict[str, asyncio.Lock] = {}
_GROK_COMPOSER_SELECTOR = '[aria-label="Prompt"][contenteditable="true"]'


def _composer_focus_js(selector: str) -> str:
    """Grok-style focus/clear script for one witnessed composer selector."""
    sel = json.dumps(selector)
    return (
        f"(()=>{{const el=document.querySelector({sel});"
        "if(!el)return{ok:false};const draft=(el.innerText||'').trim();"
        "const stale=/^BRIDGE_WAKE\\s+thread=\\S+\\s+turn=\\d+(?:,\\d+)*$/.test(draft);"
        "if(draft.length>0&&!stale)return{ok:false,reason:'composer_not_empty',draft};"
        "el.focus();const sel=window.getSelection();"
        "if(sel){const range=document.createRange();range.selectNodeContents(el);"
        "sel.removeAllRanges();sel.addRange(range);}"
        "if(stale){document.execCommand('delete',false,null);el.innerText='';}"
        "return{ok:true};})()"
    )


def _cursor_composer_selector() -> str:
    path = Path(__file__).resolve().parent / "fixtures" / "cursor_agents_9338_probe.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str(payload["composer_selector"])


_COMPOSER_JS = {
    "grok": _composer_focus_js(_GROK_COMPOSER_SELECTOR),
    "cursor": _composer_focus_js(_cursor_composer_selector()),
}


def _display_lock(display: str) -> asyncio.Lock:
    return _DISPLAY_LOCKS.setdefault(display, asyncio.Lock())


def normalize_target(display: object, app: object) -> dict[str, Any]:
    """Map one display and app to its debug-port origin."""
    disp = str(display or "").strip().lstrip(":").lower()
    name = str(app or "").strip().lower().replace("_", "-")
    if name in {"grok-bot", "grokbot"}:
        name = "grok"
    origin = _TARGETS.get((name, disp))
    if origin is None:
        return {"ok": False, "reason": "unknown_target", "display": disp, "app": name}
    return {"ok": True, "display": disp, "app": name, "origin": origin}


def _turn(value: object) -> int | str | list[int] | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 1:
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            number = int(text)
            return number if number >= 1 else None
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if parts and all(p.isdigit() and int(p) >= 1 for p in parts):
            return sorted({int(p) for p in parts})
        return None
    if isinstance(value, (list, tuple)):
        numbers: list[int] = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, int) or item < 1:
                return None
            numbers.append(item)
        return sorted(set(numbers)) if numbers else None
    return None


def _refuse(reason: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "reason": reason, "pasted": False, "delivered": False, **extra}


def _pick_page(pages: list[dict[str, str]], app: str) -> dict[str, str] | None:
    wanted = _WINDOW_TITLE.get(app, "")
    named = [
        p for p in pages if p.get("title") == wanted and p.get("web_socket_debugger_url")
    ]
    if len(named) == 1:
        return named[0]
    usable = [p for p in pages if p.get("web_socket_debugger_url")]
    return usable[0] if len(usable) == 1 else None


_STALE_WAKE_DRAFT = re.compile(r"^BRIDGE_WAKE\s+thread=\S+\s+turn=\d+(?:,\d+)*$")


def _is_stale_wake_draft(draft: str) -> bool:
    return bool(_STALE_WAKE_DRAFT.match(str(draft or "").strip()))


def _busy_or_draft_reason(surface: dict[str, Any]) -> str | None:
    # Why: grok-bot, cursor, and claude.ai all support mid-stream steering;
    # only an unsent composer draft blocks a wake paste (except a failed prior wake).
    draft = str(surface.get("composer_draft") or "").strip()
    if not draft or _is_stale_wake_draft(draft):
        return None
    return "composer_not_empty"


def _send_fallback_js(line: str) -> str:
    lj = json.dumps(line)
    sel = (
        'button[aria-label="Send message"]:not([disabled]),'
        'button[aria-label="Send"]:not([disabled]),button[type="submit"]:not([disabled])'
    )
    return (
        "(()=>{const el=document.querySelector('[aria-label=\"Prompt\"][contenteditable=\"true\"]');"
        "if(!el)return{ok:true,send_fallback_clicked:false};"
        f"const text=(el.innerText||'').trim(),line={lj};"
        "if(!text||(text!==line.trim()&&!/^BRIDGE_WAKE\\s+thread=\\S+\\s+turn=\\d+(?:,\\d+)*$/.test(text)))"
        "return{ok:true,send_fallback_clicked:false};"
        f"const btn=document.querySelector('{sel}');"
        "if(!btn)return{ok:true,send_fallback_clicked:false};btn.click();"
        "return{ok:true,send_fallback_clicked:true};})()"
    )


async def deliver_wake(
    *,
    display: object,
    app: object,
    thread: object,
    slug: object = "",
    title: object = "",
    agent_id: object = "",
    select: bool = True,
    turn: object,
) -> dict[str, Any]:
    """Select (when allowed) and paste one wake into the target sidebar chat."""
    target = normalize_target(display, app)
    if not target.get("ok"):
        return _refuse("unknown_target", display=target.get("display"), app=target.get("app"))
    number = _turn(turn)
    thread_s = str(thread or "").strip()
    slug_s = str(slug or "").strip()
    title_s = str(title or "").strip()
    aid_s = str(agent_id or "").strip()
    if number is None or not thread_s or (not title_s and not slug_s and not aid_s):
        return _refuse("bad_options", display=target["display"], app=target["app"])
    try:
        line = wake_line(thread_s, number)
    except ValueError:
        return _refuse("bad_options", display=target["display"], app=target["app"])

    async with _display_lock(target["display"]):
        return await _deliver_locked(
            target=target,
            thread_s=thread_s,
            slug_s=slug_s,
            title_s=title_s,
            aid_s=aid_s,
            select=bool(select),
            line=line,
        )


async def _deliver_locked(
    *,
    target: dict[str, Any],
    thread_s: str,
    slug_s: str,
    title_s: str,
    aid_s: str,
    select: bool,
    line: str,
) -> dict[str, Any]:
    disp, app = target["display"], target["app"]
    try:
        pages = list_pages(target["origin"])
    except URLError as exc:
        return _refuse("debug_unreachable", display=disp, app=app, detail=type(exc).__name__)
    page = _pick_page(pages, app)
    if page is None:
        return _refuse("no_page", display=disp, app=app, pages=len(pages))
    ws_url = page["web_socket_debugger_url"]
    try:
        surface = await probe_surface(ws_url)
    except (TimeoutError, RuntimeError, OSError) as exc:
        return _refuse("surface_probe_failed", display=disp, app=app, detail=type(exc).__name__)
    try:
        verdict = wake_decision(
            surface, thread=thread_s, slug=slug_s, title=title_s or None, agent_id=aid_s or None
        )
    except ValueError:
        return _refuse("bad_options", display=disp, app=app)

    if verdict.get("reason") == "ambiguous_title":
        return _refuse(
            "ambiguous_title",
            display=disp,
            app=app,
            expected_title=verdict.get("expected_title"),
            titles=verdict.get("titles"),
        )

    selected = False
    if not verdict.get("ok") and select and verdict.get("state") == "chat_list":
        guard = _busy_or_draft_reason(surface)
        if guard:
            return _refuse(
                guard,
                display=disp,
                app=app,
                active_title=verdict.get("active_title"),
                expected_title=verdict.get("expected_title"),
                expected_agent_id=verdict.get("expected_agent_id"),
            )
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
                surface, thread=thread_s, slug=slug_s, title=title_s or None, agent_id=aid_s or None
            )

    if not verdict.get("ok"):
        return {**verdict, "pasted": False, "delivered": False, "selected": selected, "display": disp, "app": app}

    guard = _busy_or_draft_reason(surface)
    if guard:
        return _refuse(
            guard,
            display=disp,
            app=app,
            selected=selected,
            active_title=verdict.get("active_title"),
            expected_title=verdict.get("expected_title"),
        )
    composer = _COMPOSER_JS.get(app)
    if composer is None:
        return {**verdict, "ok": False, "reason": "composer_unconfirmed", "pasted": False, "delivered": False, "selected": selected}
    try:
        if app == "cursor":
            paste_res = await _paste_cursor_services(
                ws_url,
                line,
                agent_id=str(verdict.get("expected_agent_id") or surface.get("active_agent_id") or ""),
            )
        else:
            paste_res = await _paste(ws_url, composer, line)
    except (TimeoutError, RuntimeError, OSError) as exc:
        return _refuse(
            "paste_failed",
            display=disp,
            app=app,
            selected=selected,
            detail=type(exc).__name__,
            expected_title=verdict.get("expected_title"),
            active_title=verdict.get("active_title"),
        )
    if not paste_res.get("ok"):
        return _refuse(
            str(paste_res.get("reason") or "no_composer"),
            display=disp,
            app=app,
            selected=selected,
            expected_title=verdict.get("expected_title"),
            active_title=verdict.get("active_title"),
        )
    await asyncio.sleep(0.25)
    post = await probe_surface(ws_url)
    delivered = bool(
        not str(post.get("composer_draft") or "").strip()
        or any(line in msg for msg in (post.get("recent_messages") or []))
    )
    return {
        **verdict,
        "ok": delivered,
        "reason": verdict.get("reason", "match") if delivered else "delivery_unconfirmed",
        "pasted": True,
        "delivered": delivered,
        "selected": selected,
        "send_fallback_clicked": bool(paste_res.get("send_fallback_clicked")),
        "display": disp,
        "app": app,
        "wake": line,
    }


async def _paste_cursor_services(
    ws_url: str, line: str, *, agent_id: str = ""
) -> dict[str, Any]:
    """Submit a wake line through Glass composer services (no Input.*)."""
    import websockets

    from cursor_bridge.glass_services_js import submit_wake_line_js

    expression = submit_wake_line_js(line, agent_id)
    async with websockets.connect(ws_url, max_size=2_000_000) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                }
            )
        )
        raw = await asyncio.wait_for(_response(ws, 1), 5.0)
    value = ((raw.get("result") or {}).get("result") or {}).get("value")
    if not isinstance(value, dict) or not value.get("ok"):
        reason = value.get("reason") if isinstance(value, dict) else "paste_failed"
        return {"ok": False, "reason": reason}
    return {"ok": True, "send_fallback_clicked": False}


async def _paste(ws_url: str, focus_js: str, line: str) -> dict[str, Any]:
    import websockets

    async with websockets.connect(ws_url, max_size=2_000_000) as ws:
        await ws.send(
            json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": focus_js, "returnByValue": True}})
        )
        focused = await asyncio.wait_for(_response(ws, 1), 5.0)
        value = ((focused.get("result") or {}).get("result") or {}).get("value")
        if not isinstance(value, dict) or not value.get("ok"):
            reason = value.get("reason") if isinstance(value, dict) and value.get("reason") else "no_composer"
            return {"ok": False, "reason": reason}
        await ws.send(json.dumps({"id": 2, "method": "Input.insertText", "params": {"text": line}}))
        await asyncio.wait_for(_response(ws, 2), 5.0)
        for mid, phase in ((3, "keyDown"), (4, "keyUp")):
            await ws.send(
                json.dumps(
                    {
                        "id": mid,
                        "method": "Input.dispatchKeyEvent",
                        "params": {
                            "type": phase,
                            "key": "Enter",
                            "code": "Enter",
                            "windowsVirtualKeyCode": 13,
                            "nativeVirtualKeyCode": 13,
                        },
                    }
                )
            )
            await asyncio.wait_for(_response(ws, mid), 5.0)
        await asyncio.sleep(0.1)
        await ws.send(
            json.dumps(
                {
                    "id": 5,
                    "method": "Runtime.evaluate",
                    "params": {"expression": _send_fallback_js(line), "returnByValue": True},
                }
            )
        )
        fallback = await asyncio.wait_for(_response(ws, 5), 5.0)
        val = ((fallback.get("result") or {}).get("result") or {}).get("value") or {}
        clicked = bool(val.get("send_fallback_clicked"))
    return {"ok": True, "send_fallback_clicked": clicked}


async def _response(ws: Any, mid: int) -> dict[str, Any]:
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            if msg.get("error"):
                raise RuntimeError(msg["error"])
            return msg
