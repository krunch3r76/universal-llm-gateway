"""Grok Bot roster manipulation over CDP (displays :9 / :10).

CDP ports come from ``config/grok-windows.json``. All agent lifecycle calls
walk the Grok renderer React client (``roster`` / ``selection`` snapshots).
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from typing import Any

from cursor_bridge.grok_computer import _cdp_eval, _resolve_ws_url
from cursor_bridge.grok_window_bindings import cdp_port_for_display

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


def _row_hidden(row: dict[str, Any]) -> bool:
    return bool(row.get("isHiddenFromSidebar") or row.get("hiddenFromSidebar"))


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
  if (!agentId) return {{ok: false, code: "create_agent_no_id", detail: created}};
  if ({hide} && typeof client.roster.setAgentHiddenFromSidebar === "function") {{
    await client.roster.setAgentHiddenFromSidebar(agentId, true);
  }}
  return {{
    ok: true,
    agent_id: agentId,
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
  const baseName = row ? String(row.name || "") : {nm};
  let nextName = baseName;
  if (!nextName.endsWith({suffix})) nextName = nextName + {suffix};
  if (typeof client.roster.updateAgent === "function") {{
    await client.roster.updateAgent({aid}, {{name: nextName}}, ["name"]);
  }}
  if (typeof client.roster.setAgentHiddenFromSidebar === "function") {{
    await client.roster.setAgentHiddenFromSidebar({aid}, true);
  }}
  return {{
    ok: true,
    agent_id: {aid},
    name: nextName,
    transport: "cdp_grok_retire",
  }};
}})()"""


async def _eval_display(
    display: str | int, expression: str, *, timeout_s: float = 30.0
) -> Any:
    port = _resolve_port(display)
    if port is None:
        raise ValueError("unknown display")
    ws_url = _resolve_ws_url(port, timeout_s=5.0)
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


def _spawn_cache_get(key: str) -> dict[str, Any] | None:
    with _SPAWN_LOCK:
        slot = _SPAWN_BY_KEY.get(key)
        if slot and slot.get("state") == "verified":
            return dict(slot.get("receipt") or {})
        return None


def _spawn_cache_begin(key: str) -> str | dict[str, Any]:
    """Return ``proceed``, a verified receipt, or an in-flight refusal."""
    with _SPAWN_LOCK:
        slot = _SPAWN_BY_KEY.get(key)
        if slot is None:
            _SPAWN_BY_KEY[key] = {"state": "in_flight"}
            return "proceed"
        if slot.get("state") == "verified":
            return dict(slot.get("receipt") or {})
        return {"ok": False, "code": "idempotency_in_flight", "duplicate": False}


def _spawn_cache_finish(key: str, receipt: dict[str, Any]) -> None:
    with _SPAWN_LOCK:
        if key:
            _SPAWN_BY_KEY[key] = {"state": "verified", "receipt": dict(receipt)}


def _spawn_cache_clear(key: str) -> None:
    with _SPAWN_LOCK:
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
    if not (mailbox and thread_scope):
        return None
    try:
        from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint
    except ImportError:
        return {"ok": False, "code": "subscription_store_unavailable"}
    try:
        return subscribe_endpoint(
            agent=str(mailbox).strip(),
            thread_scope=str(thread_scope).strip(),
            surface="grok",
            display=_display_key(display),
            title=title,
            agent_id=agent_id,
            bound_by="grok_agent_ops.spawn",
            evidence="grok_agent_ops.spawn",
        )
    except Exception as exc:
        return {
            "ok": False,
            "code": "subscription_failed",
            "reason": type(exc).__name__,
        }


def _unsubscribe_grok_rows(*, agent_id: str, display: str) -> list[dict[str, Any]]:
    try:
        from agent_bus_store.db.endpoint_subscriptions import (
            list_subscriptions,
            unsubscribe_endpoint,
        )
    except ImportError:
        return []
    disp = _display_key(display)
    aid = str(agent_id).strip()
    touched: list[dict[str, Any]] = []
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
        except Exception:
            continue
    return touched


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
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    nm = str(name or "").strip()
    if not nm:
        return {"ok": False, "code": "name_required", "reason": "name is required"}
    if _resolve_port(display) is None:
        return _unknown_display(display)

    idem = (idempotency_key or "").strip()
    if idem:
        cached = _spawn_cache_get(idem)
        if cached is not None:
            return {**cached, "duplicate": True}
        begin = _spawn_cache_begin(idem)
        if begin != "proceed":
            if begin.get("code") == "idempotency_in_flight":
                return {**begin, "display": _display_key(display)}
            return {**begin, "duplicate": True}

    nonce = idem or str(uuid.uuid4())
    try:
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
    except (OSError, TimeoutError, RuntimeError, ValueError) as exc:
        if idem:
            _spawn_cache_clear(idem)
        return {
            "ok": False,
            "code": "cdp_unreachable",
            "reason": type(exc).__name__,
            "display": _display_key(display),
        }
    if not isinstance(raw, dict) or raw.get("ok") is not True:
        if idem:
            _spawn_cache_clear(idem)
        code = str((raw or {}).get("code") or "spawn_failed")
        return {
            "ok": False,
            "code": code,
            "display": _display_key(display),
            "detail": raw,
        }

    agent_id = str(raw.get("agent_id") or "").strip()
    sel = await select_grok_agent_async(display, agent_id)
    sub = _subscribe_grok_row(
        mailbox=mailbox,
        thread_scope=thread_scope,
        display=str(display),
        agent_id=agent_id,
        title=nm,
    )

    steer_result: dict[str, Any] | None = None
    body = (text or "").strip()
    if body:
        from interlocutor.display_adapter import steer_display
        from interlocutor.targets import DisplayEightTarget

        target = DisplayEightTarget(
            display=_display_key(display), app="grok", agent_id=agent_id
        )
        steer_result = await steer_display(
            target,
            text=body,
            voice=str(voice or "self"),
            grant=None,
            authorization_source=None,
            scope=None,
        )
        if steer_result.get("ok") is not True:
            out = {
                "ok": False,
                "code": str(steer_result.get("code") or "initial_text_failed"),
                "agent_id": agent_id,
                "display": _display_key(display),
                "select": sel,
                "subscription": sub,
                "steer": steer_result,
            }
            if idem:
                _spawn_cache_clear(idem)
            return out

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


def spawn_grok_agent(
    display: str | int,
    name: str,
    **kwargs: Any,
) -> dict[str, Any]:
    return asyncio.run(spawn_grok_agent_async(display, name, **kwargs))


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
    unsubs = _unsubscribe_grok_rows(agent_id=aid, display=str(display))
    return {
        "ok": True,
        "agent_id": aid,
        "display": _display_key(display),
        "name": raw.get("name"),
        "unsubscribed": unsubs,
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
