"""Headless CDP hop for Cursor Agents on Xvfb (:8) and orion-node tunnel."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from cursor_bridge.cdp_pages import list_pages
from cursor_bridge.glass_services_js import (
    GLASS_SERVICE_MAP_FN,
    launch_new_agent_tab_js,
)

_CURSOR_AGENTS_TITLE = "Cursor Agents"
_XVFB_HOSTS = frozenset(
    {
        ":8",
        "8",
        "io:8",
        "127.0.0.1:9338",
        "http://127.0.0.1:9338",
        "orion-node",
        "127.0.0.1:19338",
        "http://127.0.0.1:19338",
    }
)
_DEBUG_ORIGIN_BY_HOST: dict[str, str] = {
    ":8": "http://127.0.0.1:9338",
    "8": "http://127.0.0.1:9338",
    "io:8": "http://127.0.0.1:9338",
    "127.0.0.1:9338": "http://127.0.0.1:9338",
    "http://127.0.0.1:9338": "http://127.0.0.1:9338",
    "orion-node": "http://127.0.0.1:19338",
    "127.0.0.1:19338": "http://127.0.0.1:19338",
    "http://127.0.0.1:19338": "http://127.0.0.1:19338",
}


def cursor_debug_url_for_gui_host(gui_host: str | None) -> str:
    """Map a GUI host label to the local CDP origin for Cursor Agents."""
    key = str(gui_host or "").strip() or ":8"
    if key in _DEBUG_ORIGIN_BY_HOST:
        return _DEBUG_ORIGIN_BY_HOST[key]
    lowered = key.lstrip(":").lower()
    if lowered == "8":
        return "http://127.0.0.1:9338"
    if lowered == "orion-node":
        return "http://127.0.0.1:19338"
    return "http://127.0.0.1:9338"


def is_cursor_xvfb_host(gui_host: str | None) -> bool:
    """True when ``gui_host`` names a Glass Cursor Agents CDP target."""
    return str(gui_host or "").strip() in _XVFB_HOSTS


def pick_chat_page(
    pages: list[dict[str, str]], *, surface: str = "cursor"
) -> dict[str, str] | None:
    """Pick the debug page for ``surface`` (cursor → Cursor Agents title)."""
    del surface  # cursor-only for now
    named = [
        p
        for p in pages
        if p.get("title") == _CURSOR_AGENTS_TITLE and p.get("web_socket_debugger_url")
    ]
    if len(named) == 1:
        return named[0]
    usable = [p for p in pages if p.get("web_socket_debugger_url")]
    return usable[0] if len(usable) == 1 else None


def _agent_repository_snapshot_js() -> str:
    """CDP expression: list agent header ids from ``agentRepositoryService``."""
    return (
        "(async () => {\n"
        f"{GLASS_SERVICE_MAP_FN}\n"
        "  const anchor = document.querySelector('[data-perf-id=\"glass-sidebar-agent-row\"]')"
        " || document.querySelector('[aria-label=\"Prompt\"][contenteditable=\"true\"]');\n"
        "  if (!anchor) return {ok: false, reason: 'no_anchor'};\n"
        "  const byId = glassServiceMap(anchor);\n"
        "  if (!byId) return {ok: false, reason: 'no_services'};\n"
        "  const repo = byId.get('agentRepositoryService');\n"
        "  if (!repo) return {ok: false, reason: 'no_repo'};\n"
        "  let headers = [];\n"
        "  if (typeof repo.getAgentHeaders === 'function') {\n"
        "    try { headers = repo.getAgentHeaders() || []; } catch (e) { headers = []; }\n"
        "  }\n"
        "  const ids = [];\n"
        "  for (const h of headers) {\n"
        "    const id = String((h && (h.id || h.agentId || h.composerId)) || '').trim();\n"
        "    if (id) ids.push(id);\n"
        "  }\n"
        "  if (!ids.length && typeof repo.getAgentHeader === 'function' && headers.length === 0) {\n"
        "    for (const el of document.querySelectorAll('[data-perf-id=\"glass-sidebar-agent-row\"]')) {\n"
        "      const id = (el.getAttribute('data-sidebar-file-drop-agent-id')"
        "        || el.getAttribute('data-agent-id') || '').trim();\n"
        "      if (id) ids.push(id);\n"
        "    }\n"
        "  }\n"
        "  return {ok: true, ids};\n"
        "})()"
    )


def launch_cursor_agents_tab(
    message: str,
    *,
    debug_url: str | None = None,
    model_query: str = "",
    model_knobs: dict[str, str] | None = None,
    timeout_s: float = 15.0,
    gui_host: str | None = None,
    requested_agent_id: str | None = None,
) -> dict[str, Any]:
    """Open a new agent via Glass services, set model and house knobs, paste, submit."""
    origin = debug_url or cursor_debug_url_for_gui_host(gui_host)
    try:
        pages = list_pages(origin, timeout_s=min(timeout_s, 5.0))
    except OSError as exc:
        return {
            "ok": False,
            "reason": "debug_unreachable",
            "detail": type(exc).__name__,
            "model_query": model_query,
        }
    page = pick_chat_page(pages, surface="cursor")
    if page is None:
        return {"ok": False, "reason": "no_page", "pages": len(pages), "model_query": model_query}
    ws_url = page["web_socket_debugger_url"]
    agent_id = (requested_agent_id or "").strip() or str(uuid.uuid4())
    return asyncio.run(
        _launch_async(
            ws_url,
            message,
            page,
            timeout_s=timeout_s,
            model_query=model_query,
            model_knobs=model_knobs,
            requested_agent_id=agent_id,
        )
    )


async def _launch_async(
    ws_url: str,
    message: str,
    page: dict[str, str],
    *,
    timeout_s: float,
    model_query: str = "",
    model_knobs: dict[str, str] | None = None,
    requested_agent_id: str = "",
) -> dict[str, Any]:
    import websockets

    expression = launch_new_agent_tab_js(
        message,
        model_query,
        model_knobs,
        requested_agent_id=requested_agent_id,
    )
    async with websockets.connect(ws_url, max_size=2_000_000) as ws:
        pre_ids: set[str] = set()
        snap = await _eval_await(ws, 1, _agent_repository_snapshot_js(), min(timeout_s, 5.0))
        if isinstance(snap, dict) and snap.get("ok"):
            pre_ids = {str(i).strip() for i in (snap.get("ids") or []) if str(i).strip()}
        result: Any = None
        launch_err: str | None = None
        try:
            result = await _eval_await(ws, 2, expression, timeout_s)
        except (TimeoutError, RuntimeError) as exc:
            launch_err = str(exc)

        def _launch_failure_recoverable(err: str | None, payload: Any) -> bool:
            blob = " ".join(
                part
                for part in (
                    err or "",
                    str(payload) if payload is not None else "",
                    json.dumps(payload) if isinstance(payload, dict) else "",
                )
                if part
            ).lower()
            return "promise was collected" in blob or "timeout" in blob

        if _launch_failure_recoverable(launch_err, result):
            recovered = await _recover_launch_after_eval_failure(
                ws,
                requested_agent_id=requested_agent_id,
                pre_ids=pre_ids,
                timeout_s=min(timeout_s, 5.0),
            )
            if recovered:
                result = recovered

        if result is None:
            return {
                "ok": False,
                "reason": "launch_eval_failed",
                "phase": "keystroke",
                "model_query": model_query,
                "detail": launch_err,
                "requested_agent_id": requested_agent_id,
            }
        if not isinstance(result, dict):
            return {
                "ok": False,
                "reason": "launch_eval_failed",
                "model_query": model_query,
                "result": result,
            }
        if not result.get("ok"):
            recovered = await _recover_launch_after_eval_failure(
                ws,
                requested_agent_id=requested_agent_id,
                pre_ids=pre_ids,
                timeout_s=min(timeout_s, 5.0),
            )
            if recovered:
                result = recovered
            else:
                result.setdefault("model_query", model_query)
                result.setdefault("phase", "keystroke")
                return result
    editor_id = str(result.get("editor_agent_id") or result.get("agent_id") or "").strip()
    return {
        "ok": True,
        "transport": "cdp_cursor_agents",
        "page_title": page.get("title"),
        "editor_agent_id": editor_id,
        "model_query": model_query,
        "model_pick": {"ok": True, "model_query": model_query} if model_query else {"ok": True, "skipped": True},
        "send_fallback_clicked": False,
        "glass_launch": result,
        "requested_agent_id": requested_agent_id,
    }


async def _recover_launch_after_eval_failure(
    ws: Any,
    *,
    requested_agent_id: str,
    pre_ids: set[str],
    timeout_s: float,
) -> dict[str, Any] | None:
    """Treat GC/timeout as success when the requested agent header already exists."""
    check_js = (
        "(async () => {\n"
        f"{GLASS_SERVICE_MAP_FN}\n"
        f"  const target = {json.dumps(requested_agent_id)};\n"
        "  const anchor = document.querySelector('[data-perf-id=\"glass-sidebar-agent-row\"]')"
        " || document.querySelector('[aria-label=\"Prompt\"][contenteditable=\"true\"]');\n"
        "  if (!anchor) return {ok: false};\n"
        "  const byId = glassServiceMap(anchor);\n"
        "  if (!byId) return {ok: false};\n"
        "  const repo = byId.get('agentRepositoryService');\n"
        "  if (!repo || typeof repo.getAgentHeader !== 'function') return {ok: false};\n"
        "  try {\n"
        "    const h = repo.getAgentHeader(target);\n"
        "    if (h) return {ok: true, agent_id: target, method: 'recovered_after_gc'};\n"
        "  } catch (e) {}\n"
        "  return {ok: false};\n"
        "})()"
    )
    header = await _eval_await(ws, 90, check_js, timeout_s)
    if isinstance(header, dict) and header.get("ok"):
        return {
            "ok": True,
            "agent_id": header.get("agent_id") or requested_agent_id,
            "editor_agent_id": header.get("agent_id") or requested_agent_id,
            "method": header.get("method") or "recovered_after_gc",
        }
    snap = await _eval_await(ws, 91, _agent_repository_snapshot_js(), timeout_s)
    if not isinstance(snap, dict) or not snap.get("ok"):
        return None
    post_ids = {str(i).strip() for i in (snap.get("ids") or []) if str(i).strip()}
    new_ids = post_ids - pre_ids
    matched = requested_agent_id if requested_agent_id in post_ids else None
    if not matched and len(new_ids) == 1:
        matched = next(iter(new_ids))
    if matched:
        return {
            "ok": True,
            "agent_id": matched,
            "editor_agent_id": matched,
            "method": "recovered_after_gc",
        }
    return None


async def _eval(ws: Any, mid: int, expression: str, timeout_s: float) -> Any:
    await ws.send(
        json.dumps(
            {
                "id": mid,
                "method": "Runtime.evaluate",
                "params": {"expression": expression, "returnByValue": True},
            }
        )
    )
    raw = await asyncio.wait_for(_recv_id(ws, mid), timeout_s)
    return ((raw.get("result") or {}).get("result") or {}).get("value")


async def _eval_await(ws: Any, mid: int, expression: str, timeout_s: float) -> Any:
    await ws.send(
        json.dumps(
            {
                "id": mid,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": expression,
                    "awaitPromise": True,
                    "returnByValue": True,
                },
            }
        )
    )
    raw = await asyncio.wait_for(_recv_id(ws, mid), timeout_s)
    return ((raw.get("result") or {}).get("result") or {}).get("value")


async def _recv_id(ws: Any, mid: int) -> dict[str, Any]:
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            if msg.get("error"):
                raise RuntimeError(msg["error"])
            return msg


from cursor_bridge.cursor_agents_archive import (  # noqa: E402
    _GLASS_ROW_PROBE_JS,
    _archive_agent_js,
    _archive_async,
    _archive_on_ws,
    _cursor_agents_ws_url,
    _list_root_sidebar_rows_js,
    _prune_predecessors_async,
    _rename_agent_row_js,
    _rename_agent_title_js,
    _root_row_pattern,
    _select_agent_js,
    archive_cursor_agents_tab,
    prune_cursor_agents_predecessors,
    rename_agent_row_in_sidebar,
    rename_cursor_agents_sidebar_title,
)

__all__ = [
    "_GLASS_ROW_PROBE_JS",
    "_archive_agent_js",
    "_archive_async",
    "_archive_on_ws",
    "_cursor_agents_ws_url",
    "_eval",
    "_eval_await",
    "_list_root_sidebar_rows_js",
    "_prune_predecessors_async",
    "_recv_id",
    "_select_agent_js",
    "archive_cursor_agents_tab",
    "cursor_debug_url_for_gui_host",
    "is_cursor_xvfb_host",
    "launch_cursor_agents_tab",
    "list_pages",
    "pick_chat_page",
    "prune_cursor_agents_predecessors",
    "rename_agent_row_in_sidebar",
    "rename_cursor_agents_sidebar_title",
    "_rename_agent_row_js",
    "_rename_agent_title_js",
    "_root_row_pattern",
]
