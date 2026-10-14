"""Deliver one display wake, or refuse it without raising.

Callers are the ``display-chat-wake`` pipeline step. The debug port is
chosen from ``app`` and ``display``; the sidebar title comes from
``read_surface``. A match pastes ``BRIDGE_WAKE thread=<id> turn=<n>``
into that app's composer and submits it. Any other outcome returns
``ok`` false. This module does not fail a pipeline run.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.error import URLError

from cursor_bridge.cdp_pages import list_pages
from cursor_bridge.chat_surface import probe_surface, wake_decision
from cursor_bridge.chat_target import wake_line

_TARGETS: dict[tuple[str, str], str] = {
    ("cursor", "8"): "http://127.0.0.1:9338",
    ("grok", "9"): "http://127.0.0.1:9339",
}
_WINDOW_TITLE = {"cursor": "Cursor Agents", "grok": "Grok Bot"}
_COMPOSER_JS = {
    "grok": r"""
(() => {
  const el = document.querySelector('[aria-label="Prompt"][contenteditable="true"]');
  if (!el) return {ok: false};
  el.focus();
  const sel = window.getSelection();
  if (sel) {
    const range = document.createRange();
    range.selectNodeContents(el);
    sel.removeAllRanges();
    sel.addRange(range);
  }
  return {ok: true};
})()
""",
}


def normalize_target(display: object, app: object) -> dict[str, Any]:
    """Map one display and app to its debug-port origin.

    ``display`` accepts ``8`` or ``:8``. ``app`` accepts ``cursor``,
    ``grok``, or ``grok-bot``. An unknown pair returns ``ok`` false and
    ``reason`` ``unknown_target``. No I/O.
    """
    disp = str(display or "").strip().lstrip(":").lower()
    name = str(app or "").strip().lower().replace("_", "-")
    if name in {"grok-bot", "grokbot"}:
        name = "grok"
    origin = _TARGETS.get((name, disp))
    if origin is None:
        return {"ok": False, "reason": "unknown_target", "display": disp, "app": name}
    return {"ok": True, "display": disp, "app": name, "origin": origin}


def _turn(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 1:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
        return number if number >= 1 else None
    return None


def _refuse(reason: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"ok": False, "reason": reason, "pasted": False}
    body.update(extra)
    return body


def _pick_page(pages: list[dict[str, str]], app: str) -> dict[str, str] | None:
    wanted = _WINDOW_TITLE.get(app, "")
    named = [
        page
        for page in pages
        if page.get("title") == wanted and page.get("web_socket_debugger_url")
    ]
    if len(named) == 1:
        return named[0]
    usable = [page for page in pages if page.get("web_socket_debugger_url")]
    if len(usable) == 1:
        return usable[0]
    return None


async def deliver_wake(
    *,
    display: object,
    app: object,
    thread: object,
    slug: object = "",
    title: object = "",
    turn: object,
) -> dict[str, Any]:
    """Paste one wake when the active sidebar title matches, else refuse.

    Returns ``ok``, ``reason``, and ``pasted``. ``title`` specifies the
    exact sidebar title to match (e.g. a Grok Bot name); when omitted,
    ``"<thread> <slug>"`` is used. A title mismatch, a down debug port, or
    a missing composer is ``ok`` false with ``pasted`` false. The only
    mutation is the paste after a match. Does not raise for those gate
    outcomes.
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
    if number is None or not thread_s or (not title_s and not slug_s):
        return _refuse("bad_options", display=target["display"], app=target["app"])
    try:
        line = wake_line(thread_s, number)
    except ValueError:
        return _refuse("bad_options", display=target["display"], app=target["app"])
    try:
        pages = list_pages(target["origin"])
    except URLError as exc:
        return _refuse(
            "debug_unreachable",
            display=target["display"],
            app=target["app"],
            detail=type(exc).__name__,
        )
    page = _pick_page(pages, target["app"])
    if page is None:
        return _refuse(
            "no_page",
            display=target["display"],
            app=target["app"],
            pages=len(pages),
        )
    try:
        surface = await probe_surface(page["web_socket_debugger_url"])
    except (TimeoutError, RuntimeError, OSError) as exc:
        return _refuse(
            "surface_probe_failed",
            display=target["display"],
            app=target["app"],
            detail=type(exc).__name__,
        )
    try:
        verdict = wake_decision(
            surface,
            thread=thread_s,
            slug=slug_s,
            title=title_s or None,
        )
    except ValueError:
        return _refuse("bad_options", display=target["display"], app=target["app"])
    if not verdict.get("ok"):
        verdict["pasted"] = False
        verdict["display"] = target["display"]
        verdict["app"] = target["app"]
        return verdict
    composer = _COMPOSER_JS.get(target["app"])
    if composer is None:
        verdict["ok"] = False
        verdict["reason"] = "composer_unconfirmed"
        verdict["pasted"] = False
        return verdict
    try:
        pasted = await _paste(page["web_socket_debugger_url"], composer, line)
    except (TimeoutError, RuntimeError, OSError) as exc:
        return _refuse(
            "paste_failed",
            display=target["display"],
            app=target["app"],
            detail=type(exc).__name__,
            expected_title=verdict.get("expected_title"),
            active_title=verdict.get("active_title"),
        )
    if not pasted:
        return _refuse(
            "no_composer",
            display=target["display"],
            app=target["app"],
            expected_title=verdict.get("expected_title"),
            active_title=verdict.get("active_title"),
        )
    verdict["pasted"] = True
    verdict["display"] = target["display"]
    verdict["app"] = target["app"]
    verdict["wake"] = line
    return verdict


async def _paste(ws_url: str, focus_js: str, line: str) -> bool:
    import websockets

    async with websockets.connect(ws_url, max_size=2_000_000) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {"expression": focus_js, "returnByValue": True},
                }
            )
        )
        focused = await asyncio.wait_for(_response(ws, 1), 5.0)
        value = ((focused.get("result") or {}).get("result") or {}).get("value")
        if not isinstance(value, dict) or not value.get("ok"):
            return False
        await ws.send(
            json.dumps(
                {"id": 2, "method": "Input.insertText", "params": {"text": line}}
            )
        )
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
    return True


async def _response(ws: Any, mid: int) -> dict[str, Any]:
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            if msg.get("error"):
                raise RuntimeError(msg["error"])
            return msg
