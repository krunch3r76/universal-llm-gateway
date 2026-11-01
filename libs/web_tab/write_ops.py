"""Web-tab write operations — open, bind, paste, submit, verify, read_reply."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

from cursor_bridge.dom_composer_paste import (
    dom_click_send,
    dom_insert_and_send,
    dom_set_composer_text,
)

from web_tab.cdp_actuation import CdpActuationPort, default_actuation_port
from web_tab.grants import require_grant
from web_tab.operator_guard import operator_active_probe
from web_tab.provider_selectors import (
    ProviderSite,
    composer_selector_for_site,
    read_reply_js,
    send_button_click_js,
    site_from_url,
    surface_for_site,
    verify_committed_js,
)
from web_tab.tab_binding_store import load_binding, save_binding
from web_tab.tab_session import tab_session_from_row
from web_tab.target_lock import TargetLockBusyError, target_write_lock

WriteOp = Literal["open", "bind", "paste", "submit", "verify_committed", "read_reply"]

ReceiptLevel = Literal["dom_paste", "dom_committed"]


def _resolve_site(
    *,
    conversation_url: str | None,
    surface: str | None,
) -> ProviderSite | None:
    if conversation_url:
        found = site_from_url(conversation_url)
        if found:
            return found
    surf = (surface or "").strip().lower()
    if surf in {"chatgpt", "openai"}:
        return "chatgpt"
    if surf in {"claude"}:
        return "claude"
    if surf in {"grok", "x_grok", "x"}:
        return "grok"
    return None


def _ws_for_target(
    port: CdpActuationPort,
    *,
    cdp_url: str,
    target_id: str,
) -> tuple[str | None, dict[str, str] | None]:
    listing = port.list_tabs(cdp_url=cdp_url)
    for row in listing.get("pages") or []:
        if str(row.get("id") or "") == target_id:
            ws = str(row.get("web_socket_debugger_url") or "") or None
            return ws, row
    return None, None


async def execute_open(
    *,
    cdp_url: str,
    url: str,
    surface: str | None = None,
    port: CdpActuationPort | None = None,
) -> dict[str, Any]:
    site = _resolve_site(conversation_url=url, surface=surface)
    if site is None:
        return {
            "ok": False,
            "code": "unsupported_url",
            "reason": "unknown provider host",
        }
    impl = port or default_actuation_port()
    opened = impl.create_background_tab(cdp_url=cdp_url, url=url)
    if not opened.get("ok"):
        return {"ok": False, **opened}
    return {
        "ok": True,
        "target_id": opened.get("target_id"),
        "url": opened.get("url"),
        "reused": bool(opened.get("reused")),
        "surface": surface_for_site(site),
        "web_socket_debugger_url": opened.get("web_socket_debugger_url"),
    }


async def execute_bind(
    *,
    cdp_url: str,
    conversation_url: str,
    target_id: str,
    surface: str | None = None,
    rebind: bool = False,
    store_path: Path | None = None,
    port: CdpActuationPort | None = None,
) -> dict[str, Any]:
    site = _resolve_site(conversation_url=conversation_url, surface=surface)
    if site is None:
        return {"ok": False, "code": "unsupported_url"}
    impl = port or default_actuation_port()
    existing = load_binding(
        cdp_url=cdp_url, conversation_url=conversation_url, path=store_path
    )
    if existing and not rebind and existing.target_id != target_id:
        return {
            "ok": False,
            "code": "already_bound",
            "reason": "conversation already bound; pass rebind=true",
            "bound_target_id": existing.target_id,
        }
    ws, row = _ws_for_target(impl, cdp_url=cdp_url, target_id=target_id)
    if row is None:
        return {"ok": False, "code": "target_not_found", "target_id": target_id}
    session = tab_session_from_row(
        target_id=target_id,
        cdp_url=cdp_url,
        conversation_url=conversation_url,
        surface=surface or surface_for_site(site),
        web_socket_debugger_url=ws,
    )
    save_binding(session, path=store_path)
    return {"ok": True, "binding": session.as_dict(), "rebind": bool(rebind)}


async def execute_paste(
    *,
    cdp_url: str,
    target_id: str,
    text: str,
    grant: str | None,
    surface: str | None = None,
    conversation_url: str | None = None,
    submit: bool = False,
    port: CdpActuationPort | None = None,
) -> dict[str, Any]:
    refused = require_grant(grant)
    if refused:
        return refused
    site = _resolve_site(conversation_url=conversation_url or "", surface=surface)
    if site is None:
        return {"ok": False, "code": "unsupported_surface"}
    impl = port or default_actuation_port()
    ws, _row = _ws_for_target(impl, cdp_url=cdp_url, target_id=target_id)
    if not ws:
        return {"ok": False, "code": "target_not_found", "target_id": target_id}
    selector = composer_selector_for_site(site)
    try:
        async with target_write_lock(target_id):
            guard = await operator_active_probe(ws, selector=selector)
            if not guard.get("ok"):
                return guard
            if submit:
                result = await dom_insert_and_send(ws, selector=selector, line=text)
            else:
                result = await dom_set_composer_text(ws, selector=selector, text=text)
    except TargetLockBusyError:
        return {
            "ok": False,
            "code": "target_lock_busy",
            "reason": "another writer holds the target lock",
            "retryable": True,
        }
    if not result.get("ok"):
        return {"ok": False, **result}
    receipt: ReceiptLevel = "dom_paste"
    if submit:
        verify = await impl.evaluate(
            ws_url=ws,
            expression=verify_committed_js(site=site, prompt_text=text),
        )
        if verify.get("receipt") == "dom_committed":
            receipt = "dom_committed"
    return {
        "ok": True,
        "receipt": receipt,
        "submitted": bool(submit),
        "target_id": target_id,
    }


async def execute_submit(
    *,
    cdp_url: str,
    target_id: str,
    grant: str | None,
    expected_text: str | None = None,
    surface: str | None = None,
    conversation_url: str | None = None,
    port: CdpActuationPort | None = None,
) -> dict[str, Any]:
    refused = require_grant(grant)
    if refused:
        return refused
    site = _resolve_site(conversation_url=conversation_url or "", surface=surface)
    if site is None:
        return {"ok": False, "code": "unsupported_surface"}
    impl = port or default_actuation_port()
    ws, _row = _ws_for_target(impl, cdp_url=cdp_url, target_id=target_id)
    if not ws:
        return {"ok": False, "code": "target_not_found"}
    selector = composer_selector_for_site(site)
    line = (expected_text or "").strip()
    try:
        async with target_write_lock(target_id):
            guard = await operator_active_probe(ws, selector=selector)
            if not guard.get("ok"):
                return guard
            if line:
                send = await dom_click_send(ws, selector=selector, line=line)
            else:
                raw = await impl.evaluate(
                    ws_url=ws, expression=send_button_click_js(site=site)
                )
                send = raw if isinstance(raw, dict) else {"ok": False}
    except TargetLockBusyError:
        return {"ok": False, "code": "target_lock_busy", "retryable": True}
    if not send.get("ok"):
        return {"ok": False, **send}
    return {"ok": True, "submitted": True, "target_id": target_id}


async def execute_verify_committed(
    *,
    cdp_url: str,
    target_id: str,
    prompt_text: str,
    surface: str | None = None,
    conversation_url: str | None = None,
    port: CdpActuationPort | None = None,
) -> dict[str, Any]:
    site = _resolve_site(conversation_url=conversation_url or "", surface=surface)
    if site is None:
        return {"ok": False, "code": "unsupported_surface"}
    impl = port or default_actuation_port()
    ws, _row = _ws_for_target(impl, cdp_url=cdp_url, target_id=target_id)
    if not ws:
        return {"ok": False, "code": "target_not_found"}
    raw = await impl.evaluate(
        ws_url=ws,
        expression=verify_committed_js(site=site, prompt_text=prompt_text),
    )
    receipt = str(raw.get("receipt") or "dom_paste")
    committed = receipt == "dom_committed"
    return {
        "ok": committed or bool(raw.get("has_user_turn")),
        "receipt": receipt,
        "dom": raw,
        "screenshot_fallback": not committed and not raw.get("has_user_turn"),
    }


async def execute_read_reply(
    *,
    cdp_url: str,
    target_id: str,
    surface: str | None = None,
    conversation_url: str | None = None,
    timeout_s: float = 45.0,
    poll_s: float = 0.75,
    port: CdpActuationPort | None = None,
) -> dict[str, Any]:
    site = _resolve_site(conversation_url=conversation_url or "", surface=surface)
    if site is None:
        return {"ok": False, "code": "unsupported_surface"}
    impl = port or default_actuation_port()
    ws, _row = _ws_for_target(impl, cdp_url=cdp_url, target_id=target_id)
    if not ws:
        return {"ok": False, "code": "target_not_found"}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    last: dict[str, Any] = {}
    while loop.time() <= deadline:
        last = await impl.evaluate(ws_url=ws, expression=read_reply_js(site=site))
        if last.get("complete"):
            return {
                "ok": True,
                "text": str(last.get("text") or ""),
                "streaming": False,
                "target_id": target_id,
            }
        if not last.get("streaming") and str(last.get("text") or ""):
            return {
                "ok": True,
                "text": str(last.get("text") or ""),
                "streaming": False,
                "target_id": target_id,
            }
        await asyncio.sleep(poll_s)
    return {
        "ok": False,
        "code": "timeout",
        "reason": "assistant reply did not complete",
        "partial_text": str(last.get("text") or ""),
        "streaming": bool(last.get("streaming")),
    }


async def dispatch_write_op(op: WriteOp, options: dict[str, Any]) -> dict[str, Any]:
    """Route pipeline/MCP options to a write op."""
    cdp_url = str(options.get("cdp_url") or "").strip()
    if not cdp_url:
        return {"ok": False, "code": "missing_cdp_url"}
    if op == "open":
        url = str(options.get("url") or options.get("conversation_url") or "")
        if not url:
            return {"ok": False, "code": "missing_url"}
        return await execute_open(
            cdp_url=cdp_url,
            url=url,
            surface=options.get("surface"),
        )
    target_id = str(options.get("target_id") or "").strip()
    if op == "bind":
        conv = str(options.get("conversation_url") or "")
        if not conv or not target_id:
            return {"ok": False, "code": "missing_bind_fields"}
        return await execute_bind(
            cdp_url=cdp_url,
            conversation_url=conv,
            target_id=target_id,
            surface=options.get("surface"),
            rebind=bool(options.get("rebind")),
        )
    if op == "paste":
        text = str(options.get("text") or options.get("body") or "")
        if not target_id or not text:
            return {"ok": False, "code": "missing_paste_fields"}
        return await execute_paste(
            cdp_url=cdp_url,
            target_id=target_id,
            text=text,
            grant=options.get("grant"),
            surface=options.get("surface"),
            conversation_url=options.get("conversation_url"),
            submit=bool(options.get("submit")),
        )
    if op == "submit":
        if not target_id:
            return {"ok": False, "code": "missing_target_id"}
        return await execute_submit(
            cdp_url=cdp_url,
            target_id=target_id,
            grant=options.get("grant"),
            expected_text=options.get("text"),
            surface=options.get("surface"),
            conversation_url=options.get("conversation_url"),
        )
    if op == "verify_committed":
        prompt = str(options.get("text") or options.get("prompt_text") or "")
        if not target_id or not prompt:
            return {"ok": False, "code": "missing_verify_fields"}
        return await execute_verify_committed(
            cdp_url=cdp_url,
            target_id=target_id,
            prompt_text=prompt,
            surface=options.get("surface"),
            conversation_url=options.get("conversation_url"),
        )
    if op == "read_reply":
        if not target_id:
            return {"ok": False, "code": "missing_target_id"}
        return await execute_read_reply(
            cdp_url=cdp_url,
            target_id=target_id,
            surface=options.get("surface"),
            conversation_url=options.get("conversation_url"),
            timeout_s=float(options.get("timeout_s") or 45.0),
        )
    return {"ok": False, "code": "unknown_op", "op": op}
