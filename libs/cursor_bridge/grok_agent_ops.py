"""Grok Bot roster manipulation over CDP (displays :9 / :10).

CDP ports come from ``config/grok-windows.json``. All agent lifecycle calls
walk the Grok renderer React client (``roster`` / ``selection`` snapshots).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import uuid
from typing import Any

from universal_logging import get_logger

from cursor_bridge.cdp_gateway_forwarder import container_cdp_origin
from cursor_bridge.cdp_pages import list_pages
from cursor_bridge.grok_computer import _cdp_eval, _pick_grok_page
from cursor_bridge.grok_window_bindings import cdp_port_for_display

logger = get_logger(__name__)

_SPAWN_LOCK = threading.Lock()
_SPAWN_BY_KEY: dict[str, dict[str, Any]] = {}

_RETIRED_SUFFIX = " (retired)"

_FIND_CLIENT = """
  const root = document.getElementById("root") || document.body.firstElementChild;
  const fk = root ? Object.keys(root).find(k => k.startsWith("__reactContainer") || k.startsWith("__reactFiber")) : null;
  if (!fk) return null;
  const stack = [root[fk]];
  const seen = new Set();
  while (stack.length) {
    const node = stack.pop();
    if (!node || seen.has(node)) continue;
    seen.add(node);
    if (node.memoizedProps?.value?.roster) return node.memoizedProps.value;
    if (node.child) stack.push(node.child);
    if (node.sibling) stack.push(node.sibling);
  }
  return null;
"""


def _display_key(display: str | int) -> str:
    return str(display or "").strip().lstrip(":")


def _resolve_port(display: str | int) -> int | None:
    key = _display_key(display)
    if not key:
        return None
    return cdp_port_for_display(key)


def _unknown_display(display: str | int) -> dict[str, Any]:
    return {
        "ok": False,
        "code": "unknown_display",
        "reason": f"unknown grok display {display!r}",
        "display": _display_key(display),
    }


def _js_list(*, include_hidden: bool, include_retired: bool) -> str:
    ih = "true" if include_hidden else "false"
    ir = "true" if include_retired else "false"
    return f"""(async () => {{
  const client = (() => {{ {_FIND_CLIENT} }})();
  if (!client) return {{ok: false, code: "grok_client_missing"}};
  const snap = client.roster?.snapshots?.get ? client.roster.snapshots.get() : null;
  const rows = snap?.agents?.rows || [];
  const includeHidden = {ih};
  const includeRetired = {ir};
  const agents = [];
  for (const r of rows) {{
    const name = String(r.name || "");
    const hidden = Boolean(r.isHiddenFromSidebar || r.hiddenFromSidebar);
    if (!includeHidden && hidden) continue;
    if (!includeRetired && name.endsWith({json.dumps(_RETIRED_SUFFIX)})) continue;
    agents.push({{
      id: r.id,
      name,
      isHiddenFromSidebar: hidden,
      isRunning: Boolean(r.isRunning),
      lastMessageId: r.lastMessageId ?? null,
    }});
  }}
  const sel = client.selection?.snapshots?.get ? client.selection.snapshots.get() : null;
  return {{
    ok: true,
    agents,
    currentAgentId: sel?.currentAgentId ?? null,
    transport: "cdp_grok_roster",
  }};
}})()"""


def _js_select(agent_id: str) -> str:
    aid = json.dumps(agent_id)
    return f"""(async () => {{
  const client = (() => {{ {_FIND_CLIENT} }})();
  if (!client?.selection?.select) return {{ok: false, code: "selection_api_missing"}};
  await client.selection.select({aid});
  const sel = client.selection.snapshots?.get ? client.selection.snapshots.get() : null;
  const current = sel?.currentAgentId ?? null;
  return {{
    ok: current === {aid},
    currentAgentId: current,
    expectedAgentId: {aid},
    transport: "cdp_grok_selection",
  }};
}})()"""


def _js_spawn(
    *,
    client_nonce: str,
    name: str,
    description: str,
    hidden: bool,
) -> str:
    payload = json.dumps(
        {
            "clientNonce": client_nonce,
            "name": name,
            "description": description,
            "creationRoute": {"kind": "box"},
            "isKickstartRequested": False,
        }
    )
    hide = "true" if hidden else "false"
    return f"""(async () => {{
  const client = (() => {{ {_FIND_CLIENT} }})();
  if (!client?.roster?.createAgent) return {{ok: false, code: "roster_api_missing"}};
  const created = await client.roster.createAgent({payload});
  const agentId = created?.id || created?.agentId || null;
  if (!agentId) return {{ok: false, code: "create_agent_no_id", detail: created, created: false}};
  if ({hide}) {{
    if (typeof client.roster.setAgentHiddenFromSidebar !== "function") {{
      return {{ok: false, code: "hide_api_missing", agent_id: agentId, created: true}};
    }}
    try {{
      await client.roster.setAgentHiddenFromSidebar(agentId, true);
    }} catch (err) {{
      return {{ok: false, code: "hide_failed", agent_id: agentId, created: true, reason: String(err)}};
    }}
  }}
  return {{
    ok: true,
    agent_id: agentId,
    created: true,
    clientNonce: {json.dumps(client_nonce)},
    hidden: {hide},
    transport: "cdp_grok_create",
  }};
}})()"""


def _js_retire(agent_id: str, name: str) -> str:
    aid = json.dumps(agent_id)
    nm = json.dumps(name)
    suffix = json.dumps(_RETIRED_SUFFIX)
    return f"""(async () => {{
  const client = (() => {{ {_FIND_CLIENT} }})();
  if (!client?.roster) return {{ok: false, code: "roster_api_missing"}};
  const rows = client.roster.snapshots?.get ? (client.roster.snapshots.get()?.agents?.rows || []) : [];
  const row = rows.find(r => r.id === {aid});
  if (!row) return {{ok: false, code: "row_not_found", agent_id: {aid}}};
  if (typeof client.roster.updateAgent !== "function" || typeof client.roster.setAgentHiddenFromSidebar !== "function") {{
    return {{ok: false, code: "renderer_api_missing", agent_id: {aid}}};
  }}
  const baseName = String(row.name || {nm});
  const nextName = baseName.endsWith({suffix}) ? baseName : baseName + {suffix};
  await client.roster.updateAgent({aid}, {{name: nextName}}, ["name"]);
  await client.roster.setAgentHiddenFromSidebar({aid}, true);
  const after = client.roster.snapshots?.get ? (client.roster.snapshots.get()?.agents?.rows || []) : [];
  const updated = after.find(r => r.id === {aid});
  const hidden = Boolean(updated && (updated.isHiddenFromSidebar || updated.hiddenFromSidebar));
  const renamed = Boolean(updated && String(updated.name || "") === nextName);
  return {{
    ok: renamed && hidden,
    code: renamed && hidden ? "retired" : "retire_unverified",
    agent_id: {aid},
    name: updated ? updated.name : nextName,
    hidden,
    renamed,
    transport: "cdp_grok_retire",
  }};
}})()"""


async def _eval_display(
    display: str | int, expression: str, *, timeout_s: float = 30.0
) -> Any:
    """Evaluate in the Grok page using the container-aware CDP origin.

    ``list_pages`` runs off the event loop. Host callers keep loopback;
    an mcp-bridge process is rewritten to the gateway forwarder.
    """
    port = _resolve_port(display)
    if port is None:
        raise ValueError("unknown display")
    origin = container_cdp_origin(f"http://127.0.0.1:{port}")

    def _pages() -> list[dict[str, str]]:
        return list_pages(origin, timeout_s=5.0)

    pages = await asyncio.to_thread(_pages)
    page = _pick_grok_page(pages)
    if page is None:
        raise OSError(f"no Grok Bot CDP page on {origin}")
    ws_url = str(page.get("web_socket_debugger_url") or "")
    if not ws_url:
        raise OSError("missing web_socket_debugger_url")
    return await _cdp_eval(ws_url, expression, await_promise=True, timeout_s=timeout_s)


async def list_grok_agents_async(
    display: str | int,
    *,
    include_hidden: bool = True,
    include_retired: bool = False,
) -> dict[str, Any]:
    if _resolve_port(display) is None:
        return _unknown_display(display)
    try:
        raw = await _eval_display(
            display,
            _js_list(include_hidden=include_hidden, include_retired=include_retired),
        )
    except (OSError, TimeoutError, RuntimeError, ValueError) as exc:
        return {
            "ok": False,
            "code": "cdp_unreachable",
            "reason": type(exc).__name__,
            "display": _display_key(display),
        }
    if not isinstance(raw, dict):
        return {
            "ok": False,
            "code": "bad_cdp_payload",
            "display": _display_key(display),
        }
    if raw.get("ok") is not True:
        return {**raw, "display": _display_key(display)}
    return {**raw, "display": _display_key(display), "cdp_port": _resolve_port(display)}


def list_grok_agents(
    display: str | int,
    *,
    include_hidden: bool = True,
    include_retired: bool = False,
) -> dict[str, Any]:
    return asyncio.run(
        list_grok_agents_async(
            display, include_hidden=include_hidden, include_retired=include_retired
        )
    )


async def select_grok_agent_async(display: str | int, agent_id: str) -> dict[str, Any]:
    aid = str(agent_id or "").strip()
    if not aid:
        return {
            "ok": False,
            "code": "agent_id_required",
            "reason": "agent_id is required",
        }
    if _resolve_port(display) is None:
        return _unknown_display(display)
    try:
        raw = await _eval_display(display, _js_select(aid))
    except (OSError, TimeoutError, RuntimeError, ValueError) as exc:
        return {
            "ok": False,
            "code": "cdp_unreachable",
            "reason": type(exc).__name__,
            "display": _display_key(display),
        }
    if not isinstance(raw, dict):
        return {
            "ok": False,
            "code": "bad_cdp_payload",
            "display": _display_key(display),
        }
    out = {**raw, "display": _display_key(display), "agent_id": aid}
    if raw.get("ok") is not True:
        out.setdefault("code", "selection_failed")
    return out


def select_grok_agent(display: str | int, agent_id: str) -> dict[str, Any]:
    return asyncio.run(select_grok_agent_async(display, agent_id))


def _spawn_digest(**parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _spawn_cache_begin(key: str, digest: str) -> str | dict[str, Any]:
    """Claim a spawn key. ``proceed`` creates; ``resume`` continues a created agent."""
    with _SPAWN_LOCK:
        slot = _SPAWN_BY_KEY.get(key)
        if slot is None:
            _SPAWN_BY_KEY[key] = {"state": "in_flight", "digest": digest}
            return "proceed"
        if slot.get("digest") != digest:
            return {"ok": False, "code": "idempotency_conflict"}
        state = str(slot.get("state") or "")
        if state == "verified":
            return dict(slot.get("receipt") or {})
        if state == "created":
            return {"resume": True, **slot}
        return {"ok": False, "code": "idempotency_in_flight", "duplicate": False}


def _spawn_cache_created(key: str, *, agent_id: str, nonce: str, digest: str) -> None:
    with _SPAWN_LOCK:
        _SPAWN_BY_KEY[key] = {
            "state": "created",
            "agent_id": agent_id,
            "nonce": nonce,
            "digest": digest,
        }


def _spawn_cache_finish(key: str, receipt: dict[str, Any]) -> None:
    with _SPAWN_LOCK:
        if key and receipt.get("ok") is True:
            prev = _SPAWN_BY_KEY.get(key) or {}
            _SPAWN_BY_KEY[key] = {
                "state": "verified",
                "digest": prev.get("digest"),
                "receipt": dict(receipt),
            }


def _spawn_cache_clear(key: str) -> None:
    with _SPAWN_LOCK:
        slot = _SPAWN_BY_KEY.get(key)
        if slot and slot.get("state") == "created":
            return
        _SPAWN_BY_KEY.pop(key, None)


def clear_spawn_cache_for_tests() -> None:
    with _SPAWN_LOCK:
        _SPAWN_BY_KEY.clear()


def _subscribe_grok_row(
    *,
    mailbox: str | None,
    thread_scope: str | None,
    display: str,
    agent_id: str,
    title: str,
) -> dict[str, Any] | None:
    """Append one grok subscription. ``None`` means the caller did not ask."""
    if not mailbox and not thread_scope:
        return None
    from agent_bus_store.db.endpoint_subscriptions import (
        SubscribeError,
        subscribe_endpoint,
    )

    try:
        result = subscribe_endpoint(
            agent=str(mailbox or "").strip(),
            thread_scope=str(thread_scope or "").strip(),
            surface="grok",
            display=_display_key(display),
            title=title,
            agent_id=agent_id,
            bound_by="grok_agent_ops.spawn",
            evidence="grok_agent_ops.spawn",
        )
    except SubscribeError as exc:
        logger.warning("grok spawn subscribe refused code=%s", exc.code)
        return {"ok": False, "code": exc.code, "reason": exc.code}
    except Exception as exc:
        logger.exception("grok spawn subscribe failed")
        return {
            "ok": False,
            "code": "subscription_failed",
            "reason": type(exc).__name__,
        }
    return {"ok": True, **(result if isinstance(result, dict) else {"row": result})}


def _unsubscribe_grok_rows(
    *, agent_id: str, display: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(unsubscribed, errors)``. Failures are logged, not dropped."""
    from agent_bus_store.db.endpoint_subscriptions import (
        list_subscriptions,
        unsubscribe_endpoint,
    )

    disp = _display_key(display)
    aid = str(agent_id).strip()
    touched: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for row in list_subscriptions():
        if row.get("state") != "subscribed":
            continue
        if str(row.get("surface") or "") != "grok":
            continue
        if str(row.get("display") or "") != disp:
            continue
        if str(row.get("agent_id") or "").strip() != aid:
            continue
        try:
            touched.append(
                unsubscribe_endpoint(
                    agent=str(row["agent"]),
                    thread_scope=str(row["thread_scope"]),
                    bound_by="grok_agent_ops.retire",
                    evidence="grok_agent_ops.retire",
                    expected_agent_id=aid,
                )
            )
        except Exception as exc:
            logger.exception(
                "grok retire unsubscribe failed agent=%s scope=%s",
                row.get("agent"),
                row.get("thread_scope"),
            )
            errors.append(
                {
                    "ok": False,
                    "code": "unsubscribe_failed",
                    "reason": type(exc).__name__,
                    "agent": row.get("agent"),
                    "thread_scope": row.get("thread_scope"),
                }
            )
    return touched, errors


def _voice_refusal(
    *, voice: str, text: str, grant: str | None, authorization_source: str | None
) -> dict[str, Any] | None:
    """Refuse a voice the steer contract cannot deliver, before any create."""
    voice_s = (voice or "self").strip().lower()
    if voice_s not in {"self", "operator_relay"}:
        return {
            "ok": False,
            "code": "unsupported_voice",
            "reason": f"unsupported voice: {voice!r}",
        }
    if voice_s == "operator_relay" and (text or "").strip():
        grant_s = (grant or "").strip().lower()
        auth = (authorization_source or "").strip()
        if grant_s not in {"operator", "explicit"} or not auth:
            return {
                "ok": False,
                "code": "watermark_missing_authorization",
                "reason": "operator_relay requires grant and authorization_source",
            }
    return None


async def spawn_grok_agent_async(
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
    """Create a Grok agent, then select, optionally subscribe, then steer.

    Authority: a key is verified only after every requested phase succeeds.
    Recovery: pre-create failure drops the key; post-create failure keeps
    ``created`` so a retry resumes and does not call ``createAgent`` again.
    """
    nm = str(name or "").strip()
    if not nm:
        return {"ok": False, "code": "name_required", "reason": "name is required"}
    if _resolve_port(display) is None:
        return _unknown_display(display)
    body = (text or "").strip()
    refused = _voice_refusal(
        voice=voice,
        text=body,
        grant=grant,
        authorization_source=authorization_source,
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

    digest = _spawn_digest(
        display=_display_key(display),
        name=nm,
        description=str(description or ""),
        hidden=bool(hidden),
        mailbox=(mailbox or "").strip(),
        thread_scope=(thread_scope or "").strip(),
        text=body,
        voice=(voice or "self").strip().lower(),
    )
    idem = (idempotency_key or "").strip()
    resume_agent = ""
    nonce = idem or str(uuid.uuid4())
    if idem:
        begin = _spawn_cache_begin(idem, digest)
        if begin == "proceed":
            pass
        elif begin.get("resume"):
            resume_agent = str(begin.get("agent_id") or "")
            nonce = str(begin.get("nonce") or nonce)
        elif begin.get("code") == "idempotency_in_flight":
            return {**begin, "display": _display_key(display)}
        elif begin.get("code"):
            return {**begin, "display": _display_key(display)}
        else:
            return {**begin, "duplicate": True}

    created = bool(resume_agent)
    agent_id = resume_agent
    try:
        if not resume_agent:
            raw = await _eval_display(
                display,
                _js_spawn(
                    client_nonce=nonce,
                    name=nm,
                    description=str(description or ""),
                    hidden=bool(hidden),
                ),
                timeout_s=60.0,
            )
            if isinstance(raw, dict) and raw.get("created") and raw.get("agent_id"):
                agent_id = str(raw["agent_id"])
                created = True
                if idem:
                    _spawn_cache_created(
                        idem, agent_id=agent_id, nonce=nonce, digest=digest
                    )
            if not isinstance(raw, dict) or raw.get("ok") is not True:
                if idem and not created:
                    _spawn_cache_clear(idem)
                return {
                    "ok": False,
                    "code": str((raw or {}).get("code") or "spawn_failed"),
                    "display": _display_key(display),
                    "agent_id": agent_id or None,
                    "detail": raw,
                }
            agent_id = str(raw.get("agent_id") or "")

        sel = await select_grok_agent_async(display, agent_id)
        sub = _subscribe_grok_row(
            mailbox=mailbox,
            thread_scope=thread_scope,
            display=str(display),
            agent_id=agent_id,
            title=nm,
        )
        if sel.get("ok") is not True:
            return {
                "ok": False,
                "code": "selection_failed",
                "agent_id": agent_id,
                "display": _display_key(display),
                "select": sel,
                "subscription": sub,
            }
        if isinstance(sub, dict) and sub.get("ok") is False:
            return {
                "ok": False,
                "code": str(sub.get("code") or "subscription_failed"),
                "agent_id": agent_id,
                "display": _display_key(display),
                "select": sel,
                "subscription": sub,
            }

        steer_result: dict[str, Any] | None = None
        if body:
            from interlocutor.display_adapter import steer_display
            from interlocutor.targets import DisplayEightTarget

            steer_result = await steer_display(
                DisplayEightTarget(
                    display=_display_key(display), app="grok", agent_id=agent_id
                ),
                text=body,
                voice=(voice or "self").strip().lower(),
                grant=grant,
                authorization_source=authorization_source,
                scope=None,
            )
            if steer_result.get("ok") is not True:
                return {
                    "ok": False,
                    "code": str(steer_result.get("code") or "initial_text_failed"),
                    "agent_id": agent_id,
                    "display": _display_key(display),
                    "select": sel,
                    "subscription": sub,
                    "steer": steer_result,
                }

        receipt = {
            "ok": True,
            "agent_id": agent_id,
            "name": nm,
            "display": _display_key(display),
            "hidden": bool(hidden),
            "clientNonce": nonce,
            "select": sel,
            "subscription": sub,
            "steer": steer_result,
            "currentAgentId": sel.get("currentAgentId"),
        }
        if idem:
            _spawn_cache_finish(idem, receipt)
        return receipt
    except asyncio.CancelledError:
        if idem and not created:
            _spawn_cache_clear(idem)
        raise
    except Exception as exc:
        logger.exception("grok spawn failed display=%s", _display_key(display))
        if idem and created:
            _spawn_cache_created(idem, agent_id=agent_id, nonce=nonce, digest=digest)
        elif idem:
            _spawn_cache_clear(idem)
        return {
            "ok": False,
            "code": "spawn_outcome_unknown" if created else "cdp_unreachable",
            "reason": type(exc).__name__,
            "display": _display_key(display),
            "agent_id": agent_id or None,
        }


def spawn_grok_agent(
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
    """Synchronous wrapper for :func:`spawn_grok_agent_async`."""
    return asyncio.run(
        spawn_grok_agent_async(
            display,
            name,
            description=description,
            hidden=hidden,
            mailbox=mailbox,
            thread_scope=thread_scope,
            text=text,
            voice=voice,
            grant=grant,
            authorization_source=authorization_source,
            idempotency_key=idempotency_key,
        )
    )


async def retire_grok_agent_async(
    display: str | int, agent_id: str, *, name: str = ""
) -> dict[str, Any]:
    aid = str(agent_id or "").strip()
    if not aid:
        return {"ok": False, "code": "agent_id_required"}
    if _resolve_port(display) is None:
        return _unknown_display(display)
    try:
        raw = await _eval_display(display, _js_retire(aid, str(name or aid)))
    except (OSError, TimeoutError, RuntimeError, ValueError) as exc:
        return {
            "ok": False,
            "code": "cdp_unreachable",
            "reason": type(exc).__name__,
            "display": _display_key(display),
        }
    if not isinstance(raw, dict) or raw.get("ok") is not True:
        return {
            "ok": False,
            "code": str((raw or {}).get("code") or "retire_failed"),
            "display": _display_key(display),
            "detail": raw,
        }
    unsubs, errors = _unsubscribe_grok_rows(agent_id=aid, display=str(display))
    ok = not errors
    return {
        "ok": ok,
        "code": "retired" if ok else "unsubscribe_partial",
        "agent_id": aid,
        "display": _display_key(display),
        "name": raw.get("name"),
        "hidden": raw.get("hidden"),
        "unsubscribed": unsubs,
        "unsubscribe_errors": errors,
    }


def retire_grok_agent(
    display: str | int, agent_id: str, *, name: str = ""
) -> dict[str, Any]:
    return asyncio.run(retire_grok_agent_async(display, agent_id, name=name))


__all__ = [
    "clear_spawn_cache_for_tests",
    "list_grok_agents",
    "list_grok_agents_async",
    "retire_grok_agent",
    "retire_grok_agent_async",
    "select_grok_agent",
    "select_grok_agent_async",
    "spawn_grok_agent",
    "spawn_grok_agent_async",
]
