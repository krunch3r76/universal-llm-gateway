"""Grant-gated operator relay paste into Grok Bot / display-chat sidebars."""

from __future__ import annotations

import asyncio
import hashlib
import json
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

OPERATOR_HEADER = "Operator request — Kaywan, relayed on his behalf"
WATERMARK = "Pasted on Kaywan's behalf by gc1 — AI-assisted"
_VALID_GRANTS = frozenset({"operator", "explicit"})


def wrap_operator_message(*, text: str, authorization_source: str, scope: str) -> str:
    """Format one operator relay body with header, source, scope, and watermark."""
    body = (text or "").strip()
    src = (authorization_source or "").strip()
    scope_s = (scope or "").strip()
    return (
        f"{OPERATOR_HEADER}\n\nSource: {src}\nScope: {scope_s}\n\n{body}\n\n{WATERMARK}"
    )


def _grant_refuse(reason: str, **extra: Any) -> dict[str, Any]:
    return {**_refuse(reason, **extra), "readback_ok": False}


async def _capture_artifacts(ws_url: str) -> dict[str, str]:
    import websockets

    dom_js = (
        "(()=>{const el=document.documentElement;return el?el.outerHTML||'':'';})()"
    )
    async with websockets.connect(ws_url, max_size=8_000_000) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {"expression": dom_js, "returnByValue": True},
                }
            )
        )
        raw_dom = await asyncio.wait_for(_cdp_response(ws, 1), 8.0)
        dom_val = ((raw_dom.get("result") or {}).get("result") or {}).get("value") or ""
        await ws.send(
            json.dumps(
                {
                    "id": 2,
                    "method": "Page.captureScreenshot",
                    "params": {"format": "png"},
                }
            )
        )
        raw_png = await asyncio.wait_for(_cdp_response(ws, 2), 8.0)
    png_b64 = ((raw_png.get("result") or {}).get("data") or "").encode("ascii")
    dom_bytes = str(dom_val).encode("utf-8", errors="replace")
    return {
        "sha256_dom": hashlib.sha256(dom_bytes).hexdigest(),
        "sha256_png": hashlib.sha256(png_b64).hexdigest() if png_b64 else "",
    }


async def _cdp_response(ws: Any, mid: int) -> dict[str, Any]:
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            return msg


def _readback_ok(
    post: dict[str, Any], *, watermark: str, pre_messages: list[str]
) -> bool:
    composer_empty = not str(post.get("composer_draft") or "").strip()
    messages = [str(m) for m in (post.get("recent_messages") or [])]
    watermark_visible = any(watermark in m for m in messages)
    assistant_began = bool(post.get("generating")) or len(messages) > len(pre_messages)
    return composer_empty and watermark_visible and assistant_began


async def deliver_operator_paste(
    *,
    display: object,
    app: object,
    agent_id: object,
    text: str,
    grant: object,
    authorization_source: object,
    scope: object = "",
    select: bool = True,
) -> dict[str, Any]:
    """Paste one grant-gated operator message; refuse without grant and source."""
    grant_s = str(grant or "").strip().lower()
    auth_src = str(authorization_source or "").strip()
    if grant_s not in _VALID_GRANTS or not auth_src:
        return _grant_refuse(
            "grant_or_source_required",
            grant=grant_s or None,
            authorization_source=auth_src or None,
        )
    aid_s = str(agent_id or "").strip()
    if not aid_s:
        return _grant_refuse("agent_id_required")
    target = normalize_target(display, app)
    if not target.get("ok"):
        return _grant_refuse(
            "unknown_target", display=target.get("display"), app=target.get("app")
        )
    if target["app"] == "cursor":
        return _grant_refuse(
            "cursor_ide_seat_forbidden", display=target["display"], app=target["app"]
        )
    line = wrap_operator_message(
        text=text,
        authorization_source=auth_src,
        scope=str(scope or "").strip(),
    )
    async with _display_lock(target["display"]):
        return await _deliver_operator_locked(
            target=target,
            aid_s=aid_s,
            select=bool(select),
            line=line,
        )


async def _deliver_operator_locked(
    *,
    target: dict[str, Any],
    aid_s: str,
    select: bool,
    line: str,
) -> dict[str, Any]:
    disp, app = target["display"], target["app"]
    try:
        pages = list_pages(target["origin"])
    except URLError as exc:
        return _grant_refuse(
            "debug_unreachable", display=disp, app=app, detail=type(exc).__name__
        )
    page = _pick_page(pages, app)
    if page is None:
        return _grant_refuse("no_page", display=disp, app=app, pages=len(pages))
    ws_url = page["web_socket_debugger_url"]
    try:
        surface = await probe_surface(ws_url)
    except (TimeoutError, RuntimeError, OSError) as exc:
        return _grant_refuse(
            "surface_probe_failed", display=disp, app=app, detail=type(exc).__name__
        )
    pre_messages = [str(m) for m in (surface.get("recent_messages") or [])]
    try:
        verdict = wake_decision(surface, agent_id=aid_s)
    except ValueError:
        return _grant_refuse("bad_options", display=disp, app=app)

    if verdict.get("reason") == "ambiguous_title":
        return _grant_refuse(
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
            return _grant_refuse(
                guard,
                display=disp,
                app=app,
                active_title=verdict.get("active_title"),
                expected_agent_id=verdict.get("expected_agent_id"),
            )
        click_res = await select_sidebar_target(
            ws_url,
            expected_title=str(verdict.get("expected_title") or ""),
            expected_agent_id=str(verdict.get("expected_agent_id") or aid_s),
        )
        if click_res.get("clicked"):
            selected = True
            await asyncio.sleep(0.25)
            surface = await probe_surface(ws_url)
            verdict = wake_decision(surface, agent_id=aid_s)

    if not verdict.get("ok"):
        return {
            **verdict,
            "pasted": False,
            "delivered": False,
            "readback_ok": False,
            "selected": selected,
            "display": disp,
            "app": app,
        }

    guard = _busy_or_draft_reason(surface)
    if guard:
        return _grant_refuse(
            guard,
            display=disp,
            app=app,
            selected=selected,
            active_title=verdict.get("active_title"),
            expected_agent_id=verdict.get("expected_agent_id"),
        )
    composer = _COMPOSER_JS.get(app)
    if composer is None:
        return _grant_refuse(
            "composer_unconfirmed",
            display=disp,
            app=app,
            selected=selected,
        )
    flat_line = " ".join(line.splitlines()).strip()
    try:
        paste_res = await _paste(ws_url, composer, flat_line)
    except (TimeoutError, RuntimeError, OSError) as exc:
        return _grant_refuse(
            "paste_failed",
            display=disp,
            app=app,
            selected=selected,
            detail=type(exc).__name__,
        )
    if not paste_res.get("ok"):
        return _grant_refuse(
            str(paste_res.get("reason") or "no_composer"),
            display=disp,
            app=app,
            selected=selected,
            expected_agent_id=verdict.get("expected_agent_id"),
            active_agent_id=verdict.get("active_agent_id"),
        )
    await asyncio.sleep(0.35)
    post = await probe_surface(ws_url)
    readback_ok = _readback_ok(post, watermark=WATERMARK, pre_messages=pre_messages)
    delivered = readback_ok
    artifacts = await _capture_artifacts(ws_url)
    return {
        **verdict,
        "ok": delivered,
        "reason": "delivered" if delivered else "readback_failed",
        "pasted": True,
        "delivered": delivered,
        "readback_ok": readback_ok,
        "composer_empty_after": not str(post.get("composer_draft") or "").strip(),
        "assistant_began": bool(post.get("generating"))
        or len(post.get("recent_messages") or []) > len(pre_messages),
        "watermark_visible": any(
            WATERMARK in str(m) for m in (post.get("recent_messages") or [])
        ),
        "selected": selected,
        "display": disp,
        "app": app,
        "agent_id": aid_s,
        "message": line,
        **artifacts,
    }


__all__ = [
    "WATERMARK",
    "OPERATOR_HEADER",
    "deliver_operator_paste",
    "wrap_operator_message",
]
