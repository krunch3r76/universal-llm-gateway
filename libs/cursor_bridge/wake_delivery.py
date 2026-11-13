"""Deliver one display wake, or refuse it without raising.

Callers are the ``display-chat-wake`` pipeline step. The debug port is
chosen from ``app`` and ``display``; the sidebar observation comes from
``probe_surface``. When ``select=True`` (default) and the target row is
present in the sidebar, the step can switch to that row provided the
currently active chat's composer is empty. A Cursor target whose own agent
is mid-turn, or whose workspace instance cannot be resolved, is refused
before any row click (``agent_running`` / ``workspace_unresolved``) so the
wake is held instead of starting a second run. A match pastes
``BRIDGE_WAKE thread=<id> turn=<n>`` into that app's composer, submits it,
and verifies delivery. A Glass timer win is not a refusal: the submit
went out and was not confirmed, so the result keeps ``pasted`` true,
``delivered`` false, and ``race_winner`` ``timer``. ``classify_display``
maps that shape to unknown, which holds the re-wake off. Any other failure
returns ``ok`` false with ``pasted`` false. This module does not fail a
pipeline run.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.error import URLError

from cursor_bridge.cdp_gateway_forwarder import container_cdp_origin
from cursor_bridge.cdp_pages import list_pages
from cursor_bridge.chat_surface import (
    probe_surface,
    select_sidebar_target,
    wake_decision,
)
from cursor_bridge.chat_target import (
    is_stale_wake_draft,
    stale_wake_lines_js,
    wake_line,
    wake_lines,
)
from cursor_bridge.display_interaction_locks import (
    agent_paste_lock,
    display_switch_lock,
    paste_lock_key,
)
from cursor_bridge.dom_composer_paste import (
    composer_selector,
    dom_insert_and_send,
)
from cursor_bridge.glass_agent_run_state import (
    probe_agent_run_state,
    run_state_hold_reason,
)
from cursor_bridge.grok_agent_ops import select_grok_agent_async
from cursor_bridge.grok_window_bindings import cdp_port_for_display

_CURSOR_TARGETS: dict[tuple[str, str], str] = {
    ("cursor", "8"): "http://127.0.0.1:9338",
    ("cursor", "orion-node"): "http://127.0.0.1:19338",
}
_WINDOW_TITLE = {"cursor": "Cursor Agents", "grok": "Grok Bot"}
_GROK_COMPOSER_SELECTOR = '[aria-label="Prompt"][contenteditable="true"]'


def _composer_focus_js(selector: str) -> str:
    """Grok-style focus/clear script for one witnessed composer selector."""
    sel = json.dumps(selector)
    return (
        f"(()=>{{const el=document.querySelector({sel});"
        "if(!el)return{ok:false};const draft=(el.innerText||'').trim();"
        f"const stale={stale_wake_lines_js('draft')};"
        "if(draft.length>0&&!stale)return{ok:false,reason:'composer_not_empty',draft};"
        "el.focus();const sel=window.getSelection();"
        "if(sel){const range=document.createRange();range.selectNodeContents(el);"
        "sel.removeAllRanges();sel.addRange(range);}"
        "if(stale){document.execCommand('delete',false,null);el.innerText='';}"
        "return{ok:true};})()"
    )


def _cursor_composer_selector() -> str:
    path = (
        Path(__file__).resolve().parent / "fixtures" / "cursor_agents_9338_probe.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str(payload["composer_selector"])


_COMPOSER_JS = {
    "grok": _composer_focus_js(_GROK_COMPOSER_SELECTOR),
    "cursor": _composer_focus_js(_cursor_composer_selector()),
}


def normalize_target(display: object, app: object) -> dict[str, Any]:
    """Map one display and app to its debug-port origin.

    The table is loopback. A process on the mcp docker bridge rewrites only
    ``FORWARDED_PORTS`` to the gateway forwarder. Host and Stargate
    callers keep ``127.0.0.1``. Unknown display/app pairs stay unknown.
    """
    disp = str(display or "").strip().lstrip(":").lower()
    name = str(app or "").strip().lower().replace("_", "-")
    if name in {"grok-bot", "grokbot"}:
        name = "grok"
    if name == "grok":
        port = cdp_port_for_display(disp)
        if port is None:
            return {"ok": False, "reason": "unknown_target", "display": disp, "app": name}
        origin = container_cdp_origin(f"http://127.0.0.1:{port}")
        return {"ok": True, "display": disp, "app": name, "origin": origin}
    origin = _CURSOR_TARGETS.get((name, disp))
    if origin is None:
        return {"ok": False, "reason": "unknown_target", "display": disp, "app": name}
    return {
        "ok": True,
        "display": disp,
        "app": name,
        "origin": container_cdp_origin(origin),
    }


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
        p
        for p in pages
        if p.get("title") == wanted and p.get("web_socket_debugger_url")
    ]
    if len(named) == 1:
        return named[0]
    usable = [p for p in pages if p.get("web_socket_debugger_url")]
    return usable[0] if len(usable) == 1 else None


def _is_stale_wake_draft(draft: str) -> bool:
    return is_stale_wake_draft(draft)


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
        '(()=>{const el=document.querySelector(\'[aria-label="Prompt"][contenteditable="true"]\');'
        "if(!el)return{ok:true,send_fallback_clicked:false};"
        f"const text=(el.innerText||'').trim(),line={lj};"
        f"const staleText={stale_wake_lines_js('text')};"
        "if(!text||(text!==line.trim()&&!staleText))"
        "return{ok:true,send_fallback_clicked:false};"
        f"const btn=document.querySelector('{sel}');"
        "if(!btn)return{ok:true,send_fallback_clicked:false};btn.click();"
        "return{ok:true,send_fallback_clicked:true};})()"
    )


def _normalize_wake_groups(
    raw: object,
) -> list[dict[str, Any]] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw:
        return None
    groups: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        thread_id = str(item.get("thread_id") or "").strip()
        if not thread_id or "\n" in thread_id:
            return None
        turn = item.get("turn")
        if turn is None:
            return None
        groups.append({"thread_id": thread_id, "turn": turn})
    return groups


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
    text: str = "",
    wake_groups: object = None,
) -> dict[str, Any]:
    """Paste one wake into the target sidebar chat and report whether it landed.

    Cursor submits through Glass with steerIntent and ignoreQueuing, only after
    the agent's handle in its own workspace instance reads idle. When the submit
    race is won by the timer,
    the return keeps ``pasted`` true, ``delivered`` false, and
    ``race_winner`` ``timer`` so callers do not treat the in-flight submit
    as never sent. ``classify_display`` then records unknown. Other failures
    set ``pasted`` false.

    Once :func:`display_switch_lock` is held, the step reads the surface
    again and re-runs the target decision and the composer guard before any
    sidebar click or paste. A human draft on that read, or an active chat
    other than the target that is not the chat the pre-lock read was about
    to leave, is a refusal: no click and no paste. When the post-lock read
    still needs that same sidebar row, selection, settle, and a second
    re-probe stay inside the lock, and the paste uses only the post-click
    surface. Post-paste delivery readback runs after the lock is released.
    """
    target = normalize_target(display, app)
    if not target.get("ok"):
        return _refuse(
            "unknown_target", display=target.get("display"), app=target.get("app")
        )
    number = _turn(turn)
    thread_s = str(thread or "").strip()
    slug_s = str(slug or "").strip()
    title_s = str(title or "").strip()
    aid_s = str(agent_id or "").strip()
    if number is None or not thread_s or (not title_s and not slug_s and not aid_s):
        return _refuse("bad_options", display=target["display"], app=target["app"])
    text_s = (text or "").strip()
    groups = _normalize_wake_groups(wake_groups)
    if wake_groups is not None and groups is None:
        return _refuse("bad_options", display=target["display"], app=target["app"])
    if text_s and groups is not None:
        return _refuse("bad_options", display=target["display"], app=target["app"])
    if text_s:
        if target["app"] != "cursor" or thread_s != "19627":
            return _refuse(
                "exchange_paste_cursor_stage_manager_only",
                display=target["display"],
                app=target["app"],
            )
        line = text_s
    elif groups is not None:
        try:
            line = wake_lines(groups, tab=aid_s or None)
        except ValueError:
            return _refuse("bad_options", display=target["display"], app=target["app"])
    else:
        try:
            line = wake_line(thread_s, number, tab=aid_s or None)
        except ValueError:
            return _refuse("bad_options", display=target["display"], app=target["app"])

    lock_id = paste_lock_key(agent_id=aid_s, thread=thread_s)
    async with agent_paste_lock(target["display"], lock_id):
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
        return _refuse(
            "debug_unreachable", display=disp, app=app, detail=type(exc).__name__
        )
    page = _pick_page(pages, app)
    if page is None:
        return _refuse("no_page", display=disp, app=app, pages=len(pages))
    ws_url = page["web_socket_debugger_url"]
    try:
        surface = await probe_surface(ws_url)
    except (TimeoutError, RuntimeError, OSError) as exc:
        return _refuse(
            "surface_probe_failed", display=disp, app=app, detail=type(exc).__name__
        )
    if app == "cursor" and aid_s:
        hold = run_state_hold_reason(await probe_agent_run_state(ws_url, aid_s))
        if hold:
            return _refuse(hold, display=disp, app=app, agent_id=aid_s)
    if _busy_or_draft_reason(surface) is None:
        try:
            from bus_watch.endpoint_wake_composer_defer import (
                notify_composer_empty_for_target,
            )

            notify_composer_empty_for_target(
                display=disp,
                surface=app,
                agent_id=aid_s or str(surface.get("active_agent_id") or ""),
                thread_scope=thread_s,
            )
        except Exception:
            pass
    try:
        verdict = wake_decision(
            surface,
            thread=thread_s,
            slug=slug_s,
            title=title_s or None,
            agent_id=aid_s or None,
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
    need_select = (
        not verdict.get("ok") and select and verdict.get("state") == "chat_list"
    )
    if need_select:
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

    if not verdict.get("ok") and not need_select:
        return {
            **verdict,
            "pasted": False,
            "delivered": False,
            "selected": selected,
            "display": disp,
            "app": app,
        }

    pre_active_title = str(surface.get("active_title") or "")
    pre_active_agent = str(surface.get("active_agent_id") or "")

    async with display_switch_lock(disp):
        try:
            surface = await probe_surface(ws_url)
        except (TimeoutError, RuntimeError, OSError) as exc:
            return _refuse(
                "surface_probe_failed",
                display=disp,
                app=app,
                detail=type(exc).__name__,
            )
        try:
            verdict = wake_decision(
                surface,
                thread=thread_s,
                slug=slug_s,
                title=title_s or None,
                agent_id=aid_s or None,
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
        # A chat switch while this wake waited must not be clicked away from.
        same_chat = (
            str(surface.get("active_title") or "") == pre_active_title
            and str(surface.get("active_agent_id") or "") == pre_active_agent
        )
        need_select = (
            not verdict.get("ok")
            and select
            and verdict.get("state") == "chat_list"
            and same_chat
        )
        if need_select and app == "grok" and aid_s and not verdict.get("ok"):
            cdp_pick = await select_grok_agent_async(disp, aid_s)
            if cdp_pick.get("ok"):
                selected = True
                await asyncio.sleep(0.15)
                try:
                    surface = await probe_surface(ws_url)
                    verdict = wake_decision(
                        surface,
                        thread=thread_s,
                        slug=slug_s,
                        title=title_s or None,
                        agent_id=aid_s or None,
                    )
                except Exception:
                    return _refuse(
                        "surface_probe_failed",
                        display=disp,
                        app=app,
                        selected=selected,
                    )
                need_select = (
                    not verdict.get("ok")
                    and select
                    and verdict.get("state") == "chat_list"
                    and same_chat
                )
        if need_select:
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
                    thread=thread_s,
                    slug=slug_s,
                    title=title_s or None,
                    agent_id=aid_s or None,
                )

        if not verdict.get("ok"):
            return {
                **verdict,
                "pasted": False,
                "delivered": False,
                "selected": selected,
                "display": disp,
                "app": app,
            }

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
            return {
                **verdict,
                "ok": False,
                "reason": "composer_unconfirmed",
                "pasted": False,
                "delivered": False,
                "selected": selected,
            }
        try:
            if app == "cursor":
                paste_res = await _paste_cursor_services(
                    ws_url,
                    line,
                    agent_id=str(
                        verdict.get("expected_agent_id")
                        or surface.get("active_agent_id")
                        or ""
                    ),
                )
            else:
                sel = composer_selector(
                    app=app,
                    agent_id=str(verdict.get("expected_agent_id") or aid_s or ""),
                )
                keep_nl = "\n" in line and _is_stale_wake_draft(line)
                paste_res = await dom_insert_and_send(
                    ws_url, selector=sel, line=line, preserve_newlines=keep_nl
                )
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
        if paste_res.get("race_winner") == "timer":
            # Submit went out and was not confirmed. Do not post-probe: the
            # script clears the draft in finally, which would look delivered.
            return {
                **verdict,
                "ok": False,
                "reason": str(paste_res.get("reason") or "delivery_unconfirmed"),
                "pasted": True,
                "delivered": False,
                "race_winner": "timer",
                "selected": selected,
                "send_fallback_clicked": bool(paste_res.get("send_fallback_clicked")),
                "display": disp,
                "app": app,
                "wake": line,
            }
        if not paste_res.get("ok"):
            return _refuse(
                str(paste_res.get("reason") or "no_composer"),
                display=disp,
                app=app,
                selected=selected,
                expected_title=verdict.get("expected_title"),
                active_title=verdict.get("active_title"),
                expected_agent_id=verdict.get("expected_agent_id"),
                active_agent_id=verdict.get("active_agent_id"),
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
        "reason": verdict.get("reason", "match")
        if delivered
        else "delivery_unconfirmed",
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
    """Submit a wake line through Glass composer services (no Input.*).

    A ``race_winner`` of ``timer`` is returned with ``pasted`` true and
    ``delivered`` false. Other ``ok`` false evaluations stay refusals and
    drop the Glass payload.
    """
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
    if isinstance(value, dict) and value.get("race_winner") == "timer":
        return {
            "ok": False,
            "reason": str(value.get("reason") or "delivery_unconfirmed"),
            "pasted": True,
            "delivered": False,
            "race_winner": "timer",
            "send_fallback_clicked": False,
        }
    if not isinstance(value, dict) or not value.get("ok"):
        if not isinstance(value, dict):
            return {"ok": False, "reason": "paste_failed"}
        raw_reason = str(value.get("reason") or "").strip()
        if raw_reason in {
            "",
            "no_composer",
            "no_handle",
            "no_agent_id",
            "no_composer_id",
        }:
            reason = "no_composer"
        else:
            reason = raw_reason
        return {"ok": False, "reason": reason}
    return {"ok": True, "send_fallback_clicked": False}


async def _paste(
    ws_url: str, focus_js: str, line: str, *, app: str = "grok"
) -> dict[str, Any]:
    """DOM-only grok paste (legacy signature; ``focus_js`` ignored)."""
    del focus_js
    sel = composer_selector(app=app, agent_id="")
    return await dom_insert_and_send(ws_url, selector=sel, line=line)


async def _response(ws: Any, mid: int) -> dict[str, Any]:
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            if msg.get("error"):
                raise RuntimeError(msg["error"])
            return msg
