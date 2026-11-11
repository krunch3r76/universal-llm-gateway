"""Spawn phase machine: hide readback, then select, subscribe, steer.

Process-local. Create-unknown and unconfirmed steer do not mutate on retry.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from urllib.error import URLError

from universal_logging import get_logger

from cursor_bridge.grok_agent_subscriptions import (
    preflight_subscriptions,
    subscribe_grok,
)
from cursor_bridge.grok_roster_js import js_ensure_hidden, js_spawn
from cursor_bridge.grok_spawn_adopt import adopt_agent_by_name
from cursor_bridge.grok_spawn_idempotency import (
    begin,
    known_not_sent,
    mark_steer,
    note,
    release,
    roster_snapshot_for,
)

logger = get_logger(__name__)

_PROVEN_NO_CREATE = frozenset({"roster_api_missing", "grok_client_missing"})


async def run_spawn(
    display: str | int,
    name: str,
    *,
    description: str = "",
    hidden: bool = True,
    mailbox: str | None = None,
    thread_scope: str | None = None,
    text: str | None = None,
    voice: str = "self",
    grant: str | None = None,
    authorization_source: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Create, hide, select, optionally subscribe, then steer.

    Hide readback runs before steer. An unknown create or an unconfirmed
    steer returns ``needs_operator`` and does not mutate on retry.
    """
    from cursor_bridge import grok_agent_ops as ops

    nm = str(name or "").strip()
    if not nm:
        return {"ok": False, "code": "name_required", "reason": "name is required"}
    if ops._port(display) is None:
        return ops._unknown(display)
    body = (text or "").strip()
    refused = ops._voice_refusal(
        voice=voice, text=body, grant=grant, authorization_source=authorization_source
    )
    if refused is not None:
        return refused
    has_box = bool((mailbox or "").strip())
    has_scope = bool((thread_scope or "").strip())
    if has_box != has_scope:
        return {
            "ok": False,
            "code": "mailbox_scope_required",
            "reason": "mailbox and thread_scope are both required to subscribe",
        }
    if has_box:
        pre = await asyncio.to_thread(preflight_subscriptions)
        if pre.get("ok") is not True:
            return {**pre, "display": ops._display_key(display)}

    digest = ops._digest(
        display=ops._display_key(display),
        name=nm,
        description=str(description or ""),
        hidden=bool(hidden),
        mailbox=(mailbox or "").strip(),
        thread_scope=(thread_scope or "").strip(),
        text=body,
        voice=(voice or "self").strip().lower(),
    )
    supplied = (idempotency_key or "").strip()
    idem = supplied or uuid.uuid4().hex
    claim = begin(idem, digest)
    action = str(claim.get("action") or "")
    if action == "duplicate":
        return {**dict(claim.get("receipt") or {}), "duplicate": True}
    if action in {"in_flight", "conflict", "blocked"}:
        return {**claim, "ok": False, "display": ops._display_key(display)}
    token = str(claim["token"])
    agent_id = str(claim.get("agent_id") or "")
    nonce = str(claim.get("nonce") or idem)
    hide_ok = bool(claim.get("hide_ok"))
    sub_done = bool(claim.get("sub_done"))
    async def _adopt() -> dict[str, Any]:
        snap = roster_snapshot_for(idem, token)
        return await adopt_agent_by_name(display, nm, pre_create_ids=snap)

    try:
        if action == "adopt":
            adopted = await _adopt()
            if adopted.get("ok") is not True:
                release(idem, token, outcome="create_unknown")
                return {
                    "ok": False,
                    "code": str(adopted.get("code") or "adopt_failed"),
                    "needs_operator": bool(adopted.get("needs_operator")),
                    "display": ops._display_key(display),
                    "detail": adopted,
                }
            agent_id = str(adopted.get("agent_id") or "")
            note(idem, token, agent_id=agent_id, nonce=nonce)
        elif action == "create":
            pre_list = await ops.list_grok_agents_async(
                display, include_hidden=True, include_retired=False
            )
            if pre_list.get("ok") is True:
                pre_ids = [
                    str(row.get("id") or "").strip()
                    for row in (pre_list.get("agents") or [])
                    if isinstance(row, dict) and str(row.get("id") or "").strip()
                ]
                note(idem, token, pre_create_roster_ids=pre_ids)
            try:
                raw = await ops._eval_display(
                    display,
                    js_spawn(
                        client_nonce=nonce,
                        name=nm,
                        description=str(description or ""),
                        hidden=bool(hidden),
                    ),
                    timeout_s=60.0,
                )
            except asyncio.CancelledError:
                release(idem, token, outcome="create_unknown")
                raise
            except ops.CdpPreSendError as exc:
                release(idem, token, outcome="drop")
                return {
                    "ok": False,
                    "code": "cdp_unreachable",
                    "reason": type(exc).__name__,
                    "display": ops._display_key(display),
                }
            except (OSError, TimeoutError, URLError, RuntimeError, ValueError) as exc:
                release(idem, token, outcome="create_unknown")
                return {
                    "ok": False,
                    "code": "create_unknown",
                    "needs_operator": True,
                    "reason": type(exc).__name__,
                    "display": ops._display_key(display),
                }
            code = str(raw.get("code") or "") if isinstance(raw, dict) else ""
            created = (
                isinstance(raw, dict)
                and raw.get("ok") is True
                and raw.get("created") is True
                and bool(raw.get("agent_id"))
            )
            if not created:
                if code in _PROVEN_NO_CREATE:
                    release(idem, token, outcome="drop")
                    return {
                        "ok": False,
                        "code": code,
                        "display": ops._display_key(display),
                        "detail": raw,
                    }
                if code == "create_agent_ambiguous":
                    release(idem, token, outcome="create_unknown")
                    return {
                        "ok": False,
                        "code": "create_unknown",
                        "needs_operator": True,
                        "reason": code,
                        "display": ops._display_key(display),
                        "detail": raw,
                    }
                adopted = await _adopt()
                if adopted.get("ok") is True:
                    agent_id = str(adopted.get("agent_id") or "")
                    note(idem, token, agent_id=agent_id, nonce=nonce)
                else:
                    release(idem, token, outcome="create_unknown")
                    return {
                        "ok": False,
                        "code": "create_unknown",
                        "needs_operator": True,
                        "reason": code or "spawn_unconfirmed",
                        "display": ops._display_key(display),
                        "detail": raw,
                        "adopt": adopted,
                    }
            elif created:
                agent_id = str(raw.get("agent_id") or "")
                note(idem, token, agent_id=agent_id, nonce=nonce)
        if not agent_id:
            release(idem, token, outcome="create_unknown")
            return {
                "ok": False,
                "code": "create_unknown",
                "needs_operator": True,
                "display": ops._display_key(display),
            }
        if bool(hidden) and not hide_ok:
            try:
                hid = await ops._eval_display(display, js_ensure_hidden(agent_id))
            except asyncio.CancelledError:
                release(idem, token, outcome="created")
                raise
            except (OSError, TimeoutError, URLError, RuntimeError, ValueError):
                release(idem, token, outcome="created")
                return {
                    "ok": False,
                    "code": "hide_unverified",
                    "agent_id": agent_id,
                    "display": ops._display_key(display),
                    "hidden": False,
                }
            if not isinstance(hid, dict) or hid.get("hidden") is not True:
                release(idem, token, outcome="created")
                return {
                    "ok": False,
                    "code": str((hid or {}).get("code") or "hide_unverified"),
                    "agent_id": agent_id,
                    "display": ops._display_key(display),
                    "hidden": False,
                }
            hide_ok = True
            note(idem, token, hide_ok=True)
        sel = await ops.select_grok_agent_async(display, agent_id)
        if sel.get("ok") is not True:
            release(idem, token, outcome="created")
            return {
                "ok": False,
                "code": "selection_failed",
                "agent_id": agent_id,
                "display": ops._display_key(display),
                "select": sel,
            }
        sub: dict[str, Any] | None = None
        if has_box and not sub_done:
            sub = await asyncio.to_thread(
                subscribe_grok,
                mailbox=str(mailbox),
                thread_scope=str(thread_scope),
                display=ops._display_key(display),
                agent_id=agent_id,
                title=nm,
            )
            if sub.get("ok") is not True:
                release(idem, token, outcome="created")
                return {
                    "ok": False,
                    "code": str(sub.get("code") or "subscription_failed"),
                    "agent_id": agent_id,
                    "display": ops._display_key(display),
                    "subscription": sub,
                }
            note(idem, token, sub_done=True)
        steer_result: dict[str, Any] | None = None
        if body:
            mark_steer(idem, token, "attempting")
            try:
                from interlocutor.display_adapter import steer_display
                from interlocutor.targets import DisplayEightTarget

                steer_result = await steer_display(
                    DisplayEightTarget(
                        display=ops._display_key(display), app="grok", agent_id=agent_id
                    ),
                    text=body,
                    voice=(voice or "self").strip().lower(),
                    grant=grant,
                    authorization_source=authorization_source,
                    scope=None,
                )
            except asyncio.CancelledError:
                release(idem, token, outcome="created")
                raise
            except Exception:
                logger.exception("grok spawn steer failed")
                release(idem, token, outcome="created")
                return {
                    "ok": False,
                    "code": "initial_text_unconfirmed",
                    "needs_operator": True,
                    "agent_id": agent_id,
                    "display": ops._display_key(display),
                }
            if steer_result.get("ok") is not True:
                if known_not_sent(steer_result):
                    mark_steer(idem, token, "not_sent")
                    release(idem, token, outcome="created")
                    return {
                        "ok": False,
                        "code": str(steer_result.get("code") or "initial_text_failed"),
                        "agent_id": agent_id,
                        "display": ops._display_key(display),
                        "steer": steer_result,
                    }
                mark_steer(idem, token, "sent_unknown")
                release(idem, token, outcome="created")
                return {
                    "ok": False,
                    "code": "initial_text_unconfirmed",
                    "needs_operator": True,
                    "agent_id": agent_id,
                    "display": ops._display_key(display),
                    "steer": steer_result,
                }
            mark_steer(idem, token, "delivered")
        receipt = {
            "ok": True,
            "agent_id": agent_id,
            "name": nm,
            "display": ops._display_key(display),
            "hidden": hide_ok if hidden else False,
            "clientNonce": nonce,
            "select": sel,
            "subscription": sub,
            "steer": steer_result,
            "currentAgentId": sel.get("currentAgentId"),
        }
        release(idem, token, outcome="verified", receipt=receipt)
        return receipt
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("grok spawn failed")
        return {
            "ok": False,
            "code": "create_unknown" if not agent_id else "spawn_outcome_unknown",
            "needs_operator": not bool(agent_id),
            "reason": type(exc).__name__,
            "agent_id": agent_id or None,
            "display": ops._display_key(display),
        }
    finally:
        release(idem, token, outcome="created" if agent_id else "create_unknown")
