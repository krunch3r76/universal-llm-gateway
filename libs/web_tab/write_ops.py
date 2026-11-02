"""Web-tab write operations — open, bind, paste, submit, verify, read_reply."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from cursor_bridge.dom_composer_paste import (
    dom_click_send,
    dom_click_send_own_draft,
    dom_insert_and_send,
    dom_set_composer_text,
)

from web_tab.cdp_actuation import CdpActuationPort, default_actuation_port
from web_tab.commit_verify import (
    capture_dom_baseline,
    flatten_paste_text,
    poll_read_reply,
    poll_verify_committed,
)
from web_tab.config import normalize_cdp_store_key, resolve_cdp_for_web_tab_options
from web_tab.events import emit_write_outcome
from web_tab.grants import require_write_authorization
from web_tab.operator_guard import operator_active_probe
from web_tab.provider_selectors import (
    ProviderSite,
    composer_selector_for_site,
    site_from_url,
    surface_for_site,
)
from web_tab.surface_adapter import (
    canonical_page_url,
    surface_not_web_tab_owned_refusal,
)
from web_tab.tab_binding_store import (
    load_binding,
    load_binding_for_target,
    save_binding,
)
from web_tab.tab_session import tab_session_from_row
from web_tab.target_lock import (
    TargetLockBusyError,
    cdp_open_bind_lock,
    target_write_lock,
)

WriteOp = Literal["open", "bind", "paste", "submit", "verify_committed", "read_reply"]

ReceiptLevel = Literal["dom_paste", "dom_committed", "dom_present"]

_CONSOLE_HOSTS = frozenset(
    {"console.anthropic.com", "platform.claude.com", "www.platform.claude.com"}
)


def _refuse_cdp_ask_owned_urls(*urls: str | None) -> dict[str, Any] | None:
    for raw in urls:
        if not raw:
            continue
        refusal = surface_not_web_tab_owned_refusal(str(raw))
        if refusal:
            return refusal
    return None


def _resolve_site(
    *,
    conversation_url: str | None,
    surface: str | None,
    target_url: str | None = None,
) -> ProviderSite | None:
    for candidate in (target_url, conversation_url):
        if candidate:
            found = site_from_url(candidate)
            if found:
                return found
    surf = (surface or "").strip().lower()
    if surf in {"chatgpt", "openai"}:
        return "chatgpt"
    if surf in {"grok", "x_grok", "x"}:
        return "grok"
    if surf in {"anthropic_console", "claude-console", "claude"}:
        for candidate in (target_url, conversation_url):
            if not candidate:
                continue
            host = (urlparse(candidate).hostname or "").lower()
            if host in _CONSOLE_HOSTS:
                return "claude"
    return None


def _ws_for_target(
    port: CdpActuationPort,
    *,
    cdp_url: str,
    target_id: str,
) -> tuple[str | None, dict[str, str] | None, ProviderSite | None]:
    listing = port.list_tabs(cdp_url=cdp_url)
    for row in listing.get("pages") or []:
        if str(row.get("id") or "") == target_id:
            ws = str(row.get("web_socket_debugger_url") or "") or None
            site = site_from_url(str(row.get("url") or ""))
            return ws, row, site
    return None, None, None


def _created_by_agent_for_target(
    *,
    cdp_url: str,
    target_id: str,
    conversation_url: str | None,
    tab_url: str | None = None,
    store_path: Path | None = None,
) -> bool:
    """True when binding marks this target as agent-opened; conservative when unknown."""
    try:
        cdp_norm = normalize_cdp_store_key(cdp_url)
    except ValueError:
        return False
    conv_candidates: list[str] = []
    for raw in (conversation_url, tab_url):
        if not raw:
            continue
        canon = canonical_page_url(str(raw).strip())
        if canon and canon not in conv_candidates:
            conv_candidates.append(canon)
    for conv in conv_candidates:
        row = load_binding(cdp_url=cdp_norm, conversation_url=conv, path=store_path)
        if row is not None and row.target_id == target_id:
            return bool(row.created_by_agent)
    row = load_binding_for_target(
        cdp_url=cdp_norm, target_id=target_id, path=store_path
    )
    if row is not None:
        return bool(row.created_by_agent)
    return False


def _cdp_refusal(raw: dict[str, Any]) -> dict[str, Any] | None:
    if raw.get("code") == "cdp_transport_error":
        return raw
    if raw.get("error"):
        return {
            "ok": False,
            "code": "cdp_transport_error",
            "reason": str(raw.get("error")),
            "retryable": True,
        }
    return None


async def execute_open(
    *,
    cdp_url: str,
    url: str,
    surface: str | None = None,
    grant: str | None = None,
    authorization_source: str | None = None,
    force_new: bool = True,
    reuse: bool = False,
    store_path: Path | None = None,
    port: CdpActuationPort | None = None,
) -> dict[str, Any]:
    refused = require_write_authorization(grant, authorization_source)
    if refused:
        return refused
    owned = _refuse_cdp_ask_owned_urls(url)
    if owned:
        return owned
    site = _resolve_site(conversation_url=url, surface=surface)
    if site is None:
        return {
            "ok": False,
            "code": "unsupported_url",
            "reason": "unknown provider host",
        }
    impl = port or default_actuation_port()
    cdp_norm = normalize_cdp_store_key(cdp_url)
    try:
        async with cdp_open_bind_lock(cdp_norm):
            opened = impl.create_background_tab(
                cdp_url=cdp_url,
                url=url,
                force_new=force_new,
                reuse=reuse,
            )
    except TargetLockBusyError:
        return {
            "ok": False,
            "code": "target_lock_busy",
            "reason": "another open/bind holds the CDP lock",
            "retryable": True,
        }
    if not opened.get("ok"):
        return {"ok": False, **opened}
    reused = bool(opened.get("reused"))
    created_by_agent = not reused
    tid = str(opened.get("target_id") or "")
    ws = str(opened.get("web_socket_debugger_url") or "") or None
    session = tab_session_from_row(
        target_id=tid,
        cdp_url=cdp_norm,
        conversation_url=url,
        surface=surface or surface_for_site(site),
        web_socket_debugger_url=ws,
        created_by_agent=created_by_agent,
    )
    save_binding(session, path=store_path)
    outcome = {
        "ok": True,
        "target_id": tid,
        "url": opened.get("url"),
        "reused": reused,
        "created_by_agent": created_by_agent,
        "surface": surface_for_site(site),
        "web_socket_debugger_url": ws,
        "binding": session.as_dict(),
    }
    emit_write_outcome(
        op="open",
        ok=True,
        grant=grant,
        authorization_source=authorization_source,
        target_id=str(outcome.get("target_id") or ""),
        code=None,
    )
    return outcome


async def execute_bind(
    *,
    cdp_url: str,
    conversation_url: str,
    target_id: str,
    surface: str | None = None,
    rebind: bool = False,
    grant: str | None = None,
    authorization_source: str | None = None,
    store_path: Path | None = None,
    port: CdpActuationPort | None = None,
) -> dict[str, Any]:
    refused = require_write_authorization(grant, authorization_source)
    if refused:
        return refused
    owned = _refuse_cdp_ask_owned_urls(conversation_url)
    if owned:
        return owned
    impl = port or default_actuation_port()
    ws, row, site = _ws_for_target(impl, cdp_url=cdp_url, target_id=target_id)
    if row is None:
        return {"ok": False, "code": "target_not_found", "target_id": target_id}
    owned = _refuse_cdp_ask_owned_urls(str(row.get("url") or ""))
    if owned:
        return owned
    site = site or _resolve_site(
        conversation_url=conversation_url,
        surface=surface,
        target_url=str(row.get("url") or ""),
    )
    if site is None:
        return {"ok": False, "code": "unsupported_url"}
    cdp_norm = normalize_cdp_store_key(cdp_url)
    try:
        async with cdp_open_bind_lock(cdp_norm):
            existing = load_binding(
                cdp_url=cdp_norm, conversation_url=conversation_url, path=store_path
            )
            if existing and not rebind and existing.target_id != target_id:
                return {
                    "ok": False,
                    "code": "already_bound",
                    "reason": "conversation already bound; pass rebind=true",
                    "bound_target_id": existing.target_id,
                }
            session = tab_session_from_row(
                target_id=target_id,
                cdp_url=cdp_norm,
                conversation_url=conversation_url,
                surface=surface or surface_for_site(site),
                web_socket_debugger_url=ws,
                created_by_agent=False,
            )
            save_binding(session, path=store_path)
    except TargetLockBusyError:
        return {
            "ok": False,
            "code": "target_lock_busy",
            "reason": "another open/bind holds the CDP lock",
            "retryable": True,
        }
    emit_write_outcome(
        op="bind",
        ok=True,
        grant=grant,
        authorization_source=authorization_source,
        target_id=target_id,
        code=None,
    )
    return {"ok": True, "binding": session.as_dict(), "rebind": bool(rebind)}


async def execute_paste(
    *,
    cdp_url: str,
    target_id: str,
    text: str,
    grant: str | None,
    authorization_source: str | None = None,
    surface: str | None = None,
    conversation_url: str | None = None,
    submit: bool = False,
    store_path: Path | None = None,
    port: CdpActuationPort | None = None,
) -> dict[str, Any]:
    refused = require_write_authorization(grant, authorization_source)
    if refused:
        return refused
    impl = port or default_actuation_port()
    ws, row, site = _ws_for_target(impl, cdp_url=cdp_url, target_id=target_id)
    if not ws or row is None:
        return {"ok": False, "code": "target_not_found", "target_id": target_id}
    owned = _refuse_cdp_ask_owned_urls(
        conversation_url,
        str(row.get("url") or ""),
    )
    if owned:
        return owned
    site = site or _resolve_site(
        conversation_url=conversation_url or "",
        surface=surface,
        target_url=str(row.get("url") or ""),
    )
    if site is None:
        return {"ok": False, "code": "unsupported_surface"}
    line = flatten_paste_text(text)
    selector = composer_selector_for_site(site)
    created_by_agent = _created_by_agent_for_target(
        cdp_url=cdp_url,
        target_id=target_id,
        conversation_url=conversation_url,
        tab_url=str(row.get("url") or ""),
        store_path=store_path,
    )
    try:
        async with target_write_lock(target_id):
            guard = await operator_active_probe(
                ws,
                selector=selector,
                created_by_agent=created_by_agent,
                lock_holder=True,
            )
            if not guard.get("ok"):
                return guard
            baseline = await capture_dom_baseline(impl, ws_url=ws, site=site)
            transport = _cdp_refusal(baseline)
            if transport:
                return transport
            base_users = int(baseline.get("user_count") or 0)
            if submit:
                result = await dom_insert_and_send(ws, selector=selector, line=line)
            else:
                result = await dom_set_composer_text(ws, selector=selector, text=line)
    except TargetLockBusyError:
        return {
            "ok": False,
            "code": "target_lock_busy",
            "reason": "another writer holds the target lock",
            "retryable": True,
        }
    if not result.get("ok"):
        emit_write_outcome(
            op="paste",
            ok=False,
            grant=grant,
            authorization_source=authorization_source,
            target_id=target_id,
            code=str(result.get("reason") or "paste_failed"),
        )
        return {"ok": False, **result}
    receipt: ReceiptLevel = "dom_paste"
    if submit:
        verify = await poll_verify_committed(
            impl,
            ws_url=ws,
            site=site,
            prompt_text=line,
            baseline_user_count=base_users,
        )
        transport = _cdp_refusal(verify)
        if transport:
            return transport
        if verify.get("receipt") == "dom_committed":
            receipt = "dom_committed"
    emit_write_outcome(
        op="paste",
        ok=True,
        grant=grant,
        authorization_source=authorization_source,
        target_id=target_id,
        code=None,
        receipt=receipt,
    )
    pre_assistant_count = int(baseline.get("assistant_count") or 0)
    pre_assistant_text = str(baseline.get("last_assistant_text") or "")
    return {
        "ok": True,
        "receipt": receipt,
        "submitted": bool(submit),
        "target_id": target_id,
        "assistant_count": pre_assistant_count,
        "last_assistant_text": pre_assistant_text,
        "created_by_agent": created_by_agent,
    }


async def execute_submit(
    *,
    cdp_url: str,
    target_id: str,
    grant: str | None,
    authorization_source: str | None = None,
    expected_text: str | None = None,
    surface: str | None = None,
    conversation_url: str | None = None,
    store_path: Path | None = None,
    port: CdpActuationPort | None = None,
) -> dict[str, Any]:
    refused = require_write_authorization(grant, authorization_source)
    if refused:
        return refused
    impl = port or default_actuation_port()
    ws, row, site = _ws_for_target(impl, cdp_url=cdp_url, target_id=target_id)
    if not ws or row is None:
        return {"ok": False, "code": "target_not_found"}
    site = site or _resolve_site(
        conversation_url=conversation_url or "",
        surface=surface,
        target_url=str(row.get("url") or ""),
    )
    if site is None:
        return {"ok": False, "code": "unsupported_surface"}
    selector = composer_selector_for_site(site)
    line = flatten_paste_text(expected_text or "")
    created_by_agent = _created_by_agent_for_target(
        cdp_url=cdp_url,
        target_id=target_id,
        conversation_url=conversation_url,
        tab_url=str(row.get("url") or ""),
        store_path=store_path,
    )
    try:
        async with target_write_lock(target_id):
            guard = await operator_active_probe(
                ws,
                selector=selector,
                created_by_agent=created_by_agent,
                lock_holder=True,
            )
            if not guard.get("ok"):
                return guard
            if line:
                send = await dom_click_send(ws, selector=selector, line=line)
            else:
                send = await dom_click_send_own_draft(ws, selector=selector)
    except TargetLockBusyError:
        return {"ok": False, "code": "target_lock_busy", "retryable": True}
    if not send.get("ok"):
        emit_write_outcome(
            op="submit",
            ok=False,
            grant=grant,
            authorization_source=authorization_source,
            target_id=target_id,
            code=str(send.get("reason") or "submit_failed"),
        )
        return {"ok": False, **send}
    emit_write_outcome(
        op="submit",
        ok=True,
        grant=grant,
        authorization_source=authorization_source,
        target_id=target_id,
        code=None,
    )
    return {"ok": True, "submitted": True, "target_id": target_id}


async def execute_verify_committed(
    *,
    cdp_url: str,
    target_id: str,
    prompt_text: str,
    surface: str | None = None,
    conversation_url: str | None = None,
    baseline_user_count: int | None = None,
    port: CdpActuationPort | None = None,
) -> dict[str, Any]:
    impl = port or default_actuation_port()
    ws, row, site = _ws_for_target(impl, cdp_url=cdp_url, target_id=target_id)
    if not ws or row is None:
        return {"ok": False, "code": "target_not_found"}
    site = site or _resolve_site(
        conversation_url=conversation_url or "",
        surface=surface,
        target_url=str(row.get("url") or ""),
    )
    if site is None:
        return {"ok": False, "code": "unsupported_surface"}
    baseline = await capture_dom_baseline(impl, ws_url=ws, site=site)
    pre_send_baseline = baseline_user_count is not None
    if pre_send_baseline:
        base_users = int(baseline_user_count)
    else:
        base_users = int(baseline.get("user_count") or 0)
    flat = flatten_paste_text(prompt_text)
    out = await poll_verify_committed(
        impl,
        ws_url=ws,
        site=site,
        prompt_text=flat,
        baseline_user_count=base_users,
        timeout_s=8.0,
    )
    receipt = str(out.get("receipt") or "dom_paste")
    if not pre_send_baseline and receipt == "dom_committed":
        receipt = "dom_present"
    committed = receipt == "dom_committed"
    return {
        "ok": committed or receipt == "dom_present" or bool(out.get("has_user_turn")),
        "receipt": receipt,
        "dom": out,
        "screenshot_fallback": not committed
        and receipt != "dom_present"
        and not out.get("has_user_turn"),
    }


async def execute_read_reply(
    *,
    cdp_url: str,
    target_id: str,
    surface: str | None = None,
    conversation_url: str | None = None,
    timeout_s: float = 45.0,
    poll_s: float = 0.75,
    baseline_assistant_count: int | None = None,
    baseline_assistant_text: str | None = None,
    port: CdpActuationPort | None = None,
) -> dict[str, Any]:
    impl = port or default_actuation_port()
    ws, row, site = _ws_for_target(impl, cdp_url=cdp_url, target_id=target_id)
    if not ws or row is None:
        return {"ok": False, "code": "target_not_found"}
    site = site or _resolve_site(
        conversation_url=conversation_url or "",
        surface=surface,
        target_url=str(row.get("url") or ""),
    )
    if site is None:
        return {"ok": False, "code": "unsupported_surface"}
    if baseline_assistant_count is not None:
        base_count = int(baseline_assistant_count)
        base_text = str(baseline_assistant_text or "")
    else:
        baseline = await capture_dom_baseline(impl, ws_url=ws, site=site)
        base_count = int(baseline.get("assistant_count") or 0)
        base_text = str(baseline.get("last_assistant_text") or "")
    polled = await poll_read_reply(
        impl,
        ws_url=ws,
        site=site,
        baseline_assistant_count=base_count,
        baseline_assistant_text=base_text,
        timeout_s=timeout_s,
        poll_s=poll_s,
    )
    if polled.get("ok"):
        return {
            "ok": True,
            "text": str(polled.get("text") or ""),
            "streaming": False,
            "target_id": target_id,
        }
    return {
        "ok": False,
        "code": polled.get("code") or "timeout",
        "reason": polled.get("reason") or "assistant reply did not complete",
        "partial_text": str(polled.get("partial_text") or ""),
        "streaming": bool(polled.get("streaming")),
    }


def _resolve_write_cdp_url(
    options: dict[str, Any],
) -> tuple[str | None, dict[str, Any] | None]:
    try:
        return (
            resolve_cdp_for_web_tab_options(
                surface=options.get("surface"),
                cdp_url=options.get("cdp_url"),
            ),
            None,
        )
    except ValueError as exc:
        msg = str(exc)
        if msg in {"missing_cdp_url", "cdp_surface_mismatch"}:
            return None, {"ok": False, "code": msg}
        if msg.startswith("invalid_cdp_url"):
            return None, {"ok": False, "code": "invalid_cdp_url", "reason": msg}
        raise


async def dispatch_write_op(op: WriteOp, options: dict[str, Any]) -> dict[str, Any]:
    """Route pipeline/MCP options to a write op."""
    cdp_url, cdp_err = _resolve_write_cdp_url(options)
    if cdp_err is not None:
        return cdp_err
    assert cdp_url is not None
    grant = options.get("grant")
    authorization_source = options.get("authorization_source")
    if op == "open":
        url = str(options.get("url") or options.get("conversation_url") or "")
        if not url:
            return {"ok": False, "code": "missing_url"}
        return await execute_open(
            cdp_url=cdp_url,
            url=url,
            surface=options.get("surface"),
            grant=grant,
            authorization_source=authorization_source,
            force_new=bool(options.get("force_new", True)),
            reuse=bool(options.get("reuse")),
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
            grant=grant,
            authorization_source=authorization_source,
        )
    if op == "paste":
        text = str(options.get("text") or options.get("body") or "")
        if not target_id or not text:
            return {"ok": False, "code": "missing_paste_fields"}
        return await execute_paste(
            cdp_url=cdp_url,
            target_id=target_id,
            text=text,
            grant=grant,
            authorization_source=authorization_source,
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
            grant=grant,
            authorization_source=authorization_source,
            expected_text=options.get("text"),
            surface=options.get("surface"),
            conversation_url=options.get("conversation_url"),
        )
    if op == "verify_committed":
        prompt = str(options.get("text") or options.get("prompt_text") or "")
        if not target_id or not prompt:
            return {"ok": False, "code": "missing_verify_fields"}
        buc = options.get("baseline_user_count")
        return await execute_verify_committed(
            cdp_url=cdp_url,
            target_id=target_id,
            prompt_text=prompt,
            surface=options.get("surface"),
            conversation_url=options.get("conversation_url"),
            baseline_user_count=int(buc) if buc is not None else None,
        )
    if op == "read_reply":
        if not target_id:
            return {"ok": False, "code": "missing_target_id"}
        bac = options.get("baseline_assistant_count")
        bat = options.get("baseline_assistant_text")
        return await execute_read_reply(
            cdp_url=cdp_url,
            target_id=target_id,
            surface=options.get("surface"),
            conversation_url=options.get("conversation_url"),
            timeout_s=float(options.get("timeout_s") or 45.0),
            baseline_assistant_count=int(bac) if bac is not None else None,
            baseline_assistant_text=str(bat) if bat is not None else None,
        )
    return {"ok": False, "code": "unknown_op", "op": op}
