"""Cursor Agents sidebar archive/prune via CDP (Glass :8)."""

from __future__ import annotations

import json
import re
import time
from typing import Any

_GLASS_ROW_PROBE_JS = (
    "const rowTitle=(el)=>{const t=el.querySelector('[class*=\"agent-item__title\"]');"
    "if(t){const x=(t.textContent||'').replace(/\\s+/g,' ').trim();if(x)return x;}"
    "const b=el.querySelector('button[data-agent-id]');"
    "if(b&&b.getAttribute('aria-label'))return b.getAttribute('aria-label').split(',')[0].trim();"
    "return(el.innerText||'').trim().split('\\n')[0].trim();};"
    "const rowActive=(el)=>el.getAttribute('data-active')==='true'"
    "||!!el.querySelector('.ui-sidebar-menu-button-trigger[aria-current=\"page\"]')"
    "||el.getAttribute('aria-selected')==='true'||el.getAttribute('aria-current')==='true'"
    "||!!el.querySelector('[aria-current=\"page\"],[aria-selected=\"true\"]');"
    "const rowAgentId=(el)=>(el.getAttribute('data-sidebar-file-drop-agent-id')"
    "||el.getAttribute('data-agent-id')||'').trim();"
    "const fiberKey=(el)=>{for(const k of Object.keys(el)){if(k.startsWith('__reactFiber$'))return k;}"
    "return null;};"
    "let _glassServices=null;"
    "const glassServices=(startEl)=>{if(_glassServices!==null)return _glassServices;"
    "const fk=fiberKey(startEl);if(!fk){_glassServices={};return _glassServices;}"
    "let f=startEl[fk];for(let i=0;i<170&&f;i++,f=f.return){const mp=f.memoizedProps;"
    "const entries=mp&&mp.value&&mp.value.workspace&&mp.value.workspace.instantiationService"
    "&&mp.value.workspace.instantiationService._services"
    "&&mp.value.workspace.instantiationService._services._entries;"
    "if(entries&&typeof entries.entries==='function'){let agentRepositoryService=null,backgroundWorkService=null;"
    "for(const [,ent] of entries.entries()){const inst=ent&&(ent.instance!==undefined?ent.instance:ent);"
    "if(!inst)continue;"
    "if(typeof inst.getAgentHeader==='function')agentRepositoryService=inst;"
    "if(typeof inst.getComposerBackgroundWork==='function')backgroundWorkService=inst;}"
    "_glassServices={agentRepositoryService,backgroundWorkService};return _glassServices;}}"
    "_glassServices={};return _glassServices;};"
    "const rowMeta=(el,aid)=>{const fk=fiberKey(el);let headerStatus='',createdAt=0,"
    "hasBg=false,hasSub=false,headerFound=false,glassFound=false,domBusy=false;"
    "if(fk){let f=el[fk];for(let i=0;i<170&&f;i++,f=f.return){const p=f.memoizedProps;"
    "if(!p)continue;"
    "if(p.header?.status!==undefined||p.status!==undefined){headerFound=true;"
    "headerStatus=(p.header&&((p.header.status&&p.header.status.value)||p.header.status))||p.status||'';"
    "createdAt=Number((p.header&&p.header.createdAt)||0);"
    "hasBg=Boolean(p.hasActiveBackgroundWork);hasSub=Boolean(p.hasRunningSubagentWork);}"
    "if(p.backgroundWorkService||p.composerDataService||p.subagentService){glassFound=true;}}}"
    "const svc=glassServices(el);let bgWork=[],svcInProgress=false;"
    "if(aid&&svc.backgroundWorkService"
    "&&typeof svc.backgroundWorkService.getComposerBackgroundWork==='function'){try{"
    "const bw=svc.backgroundWorkService.getComposerBackgroundWork(aid);"
    "if(Array.isArray(bw))bgWork=bw;}catch(e){}}"
    "if(aid&&svc.agentRepositoryService&&typeof svc.agentRepositoryService.getAgentHeader==='function'){"
    "try{const h=svc.agentRepositoryService.getAgentHeader(aid);"
    "if(h&&h.status&&h.status.value==='in_progress')svcInProgress=true;}catch(e){}}"
    "const unknown=!fk||(!headerFound&&!glassFound&&!domBusy);"
    "return{headerStatus,createdAt,hasBg,hasSub,bgWork,svcInProgress,unknown,headerFound,glassFound};};"
    "const rowStreaming=(el)=>{"
    "if(Boolean(el.querySelector('[aria-label=\"In progress\"], .ui-dot-grid-loader')))return true;"
    "if(el.getAttribute('data-loading')==='true')return true;const aid=rowAgentId(el);"
    "const meta=rowMeta(el,aid);if(meta.unknown)return true;"
    "if(meta.headerStatus==='in_progress'||meta.svcInProgress)return true;"
    "if(meta.hasBg||meta.hasSub)return true;if(meta.bgWork.length>0)return true;return false;};"
)


def _root_row_pattern(root_id: str) -> str:
    """Regex body (no flags) matching root sidebar titles, including ``[retired]`` prefix."""
    esc = re.escape(str(root_id or "").strip())
    return rf"(?:\[retired\]\s*)?\.?\s*{esc}\b|^resume\s+{esc}\b"


def _archive_agent_js(agent_id: str) -> str:
    aid = json.dumps(agent_id.strip())
    return (
        f"(() => {{ const target = {aid};"
        f"{_GLASS_ROW_PROBE_JS}"
        " for (const el of document.querySelectorAll('[data-perf-id=\"glass-sidebar-agent-row\"]')) {"
        " if (rowAgentId(el) !== target) continue;"
        " if (rowStreaming(el)) return {ok: false, reason: 'agent_streaming', agent_id: target};"
        " const btn = el.querySelector('button[aria-label=\"Archive\"]');"
        " if (!btn) return {ok: false, reason: 'no_archive_btn'};"
        " btn.click(); return {ok: true, archived: target};"
        " } return {ok: false, reason: 'row_not_found'}; })()"
    )


def _select_agent_js(agent_id: str) -> str:
    aid = json.dumps(agent_id.strip())
    return (
        f"(() => {{ const target = {aid};"
        f"{_GLASS_ROW_PROBE_JS}"
        " for (const el of document.querySelectorAll('[data-perf-id=\"glass-sidebar-agent-row\"]')) {"
        " if (rowAgentId(el) !== target) continue;"
        " if (!rowActive(el)) el.click();"
        " return {ok: true, selected: target};"
        " } return {ok: false, reason: 'row_not_found'}; })()"
    )


def _list_root_sidebar_rows_js(root_id: str) -> str:
    rid = json.dumps(root_id.strip())
    pattern = json.dumps(_root_row_pattern(root_id))
    return (
        f"(() => {{ const rootId = {rid};"
        f"{_GLASS_ROW_PROBE_JS}"
        f" const re = new RegExp('^' + {pattern}, 'i');"
        " const out = [];"
        " for (const el of document.querySelectorAll('[data-perf-id=\"glass-sidebar-agent-row\"]')) {"
        " const title = rowTitle(el); if (!re.test(title)) continue;"
        " const agent_id = rowAgentId(el);"
        " const meta = rowMeta(el, agent_id);"
        " out.push({agent_id, title, active: rowActive(el), streaming: rowStreaming(el),"
        " unknown: meta.unknown, created_at: meta.createdAt, sidebar_index: out.length});"
        " } return out; })()"
    )


def _rename_departing_agent_js(agent_id: str, new_title: str) -> str:
    from cursor_bridge.glass_services_js import rename_composer_title_js

    return rename_composer_title_js(agent_id, new_title)


def _rename_agent_row_js(agent_id: str, new_title: str) -> str:
    return _rename_departing_agent_js(agent_id, new_title)


_rename_agent_title_js = _rename_agent_row_js


def rename_agent_row_in_sidebar(
    agent_id: str,
    new_title: str,
    *,
    debug_url: str = "http://127.0.0.1:9338",
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    """Rename one Glass sidebar row (DOM + best-effort workbench title sync)."""
    if not str(agent_id or "").strip() or not str(new_title or "").strip():
        return {"ok": False, "reason": "empty_agent_id_or_title"}
    ws_url, err = _cursor_agents_ws_url(debug_url, timeout_s)
    if err:
        return err
    assert ws_url is not None
    return _hop.asyncio.run(_rename_title_async(ws_url, agent_id.strip(), new_title.strip(), timeout_s))


async def _rename_title_async(
    ws_url: str, agent_id: str, new_title: str, *, timeout_s: float
) -> dict[str, Any]:
    import websockets

    async with websockets.connect(ws_url, max_size=2_000_000) as ws:
        result = await _hop._eval_await(
            ws, 1, _rename_agent_row_js(agent_id, new_title), timeout_s
        )
        return result if isinstance(result, dict) else {"ok": False, "result": result}


rename_cursor_agents_sidebar_title = rename_agent_row_in_sidebar


def _cursor_agents_ws_url(
    debug_url: str, timeout_s: float
) -> tuple[str | None, dict[str, Any] | None]:
    try:
        pages = _hop.list_pages(debug_url, timeout_s=min(timeout_s, 5.0))
    except OSError as exc:
        return None, {"ok": False, "reason": "debug_unreachable", "detail": type(exc).__name__}
    page = _hop.pick_chat_page(pages, surface="cursor")
    if page is None:
        return None, {"ok": False, "reason": "no_page", "pages": len(pages)}
    return page["web_socket_debugger_url"], None


def archive_cursor_agents_tab(
    agent_id: str,
    *,
    debug_url: str = "http://127.0.0.1:9338",
    timeout_s: float = 10.0,
    wait_until_idle_s: float = 60.0,
    poll_interval_s: float = 0.5,
) -> dict[str, Any]:
    """Archive a retired agent row in the Cursor Agents sidebar via CDP."""
    if not str(agent_id or "").strip():
        return {"ok": False, "reason": "empty_agent_id"}
    ws_url, err = _cursor_agents_ws_url(debug_url, timeout_s)
    if err:
        return err
    assert ws_url is not None
    return _hop.asyncio.run(
        _archive_async(
            ws_url,
            agent_id.strip(),
            timeout_s=timeout_s,
            wait_until_idle_s=wait_until_idle_s,
            poll_interval_s=poll_interval_s,
        )
    )


async def _archive_on_ws(
    ws: Any,
    agent_id: str,
    *,
    timeout_s: float,
    wait_until_idle_s: float,
    poll_interval_s: float,
    eval_id: int = 1,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, wait_until_idle_s)
    while True:
        result = await _hop._eval(ws, eval_id, _archive_agent_js(agent_id), timeout_s)
        if isinstance(result, dict):
            if result.get("ok") or result.get("reason") != "agent_streaming":
                return result
            if time.monotonic() >= deadline:
                return result
        else:
            return {"ok": False, "reason": "archive_eval_failed", "result": result}
        await _hop.asyncio.sleep(poll_interval_s)


async def _archive_async(
    ws_url: str,
    agent_id: str,
    *,
    timeout_s: float,
    wait_until_idle_s: float = 60.0,
    poll_interval_s: float = 0.5,
) -> dict[str, Any]:
    import websockets

    async with websockets.connect(ws_url, max_size=2_000_000) as ws:
        return await _archive_on_ws(
            ws,
            agent_id,
            timeout_s=timeout_s,
            wait_until_idle_s=wait_until_idle_s,
            poll_interval_s=poll_interval_s,
        )


def prune_cursor_agents_predecessors(
    root_id: str,
    *,
    active_agent_id: str = "",
    departing_agent_id: str = "",
    holder_agent_id: str = "",
    keep_recent_predecessors: int = 2,
    debug_url: str = "http://127.0.0.1:9338",
    timeout_s: float = 10.0,
    wait_until_idle_s: float = 0.0,
    poll_interval_s: float = 0.5,
    select_active: bool = True,
    only_retired: bool = False,
    retired_agent_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Archive older root predecessors; keep recent ones active in the sidebar."""
    root = str(root_id or "").strip()
    if not root:
        return {"ok": False, "reason": "empty_root_id"}
    ws_url, err = _cursor_agents_ws_url(debug_url, timeout_s)
    if err:
        return err
    assert ws_url is not None
    try:
        return _hop.asyncio.run(
            _prune_predecessors_async(
                ws_url,
                root,
                active_agent_id=active_agent_id,
                departing_agent_id=departing_agent_id,
                holder_agent_id=holder_agent_id,
                keep_recent_predecessors=keep_recent_predecessors,
                timeout_s=timeout_s,
                wait_until_idle_s=wait_until_idle_s,
                poll_interval_s=poll_interval_s,
                select_active=select_active,
                only_retired=only_retired,
                retired_agent_ids=retired_agent_ids,
            )
        )
    except (Exception, SystemExit) as exc:
        return {
            "ok": False,
            "reason": f"cleanup_failed:{type(exc).__name__}",
            "archived": [],
            "skipped": [],
        }


async def _prune_predecessors_async(
    ws_url: str,
    root_id: str,
    *,
    active_agent_id: str,
    departing_agent_id: str,
    holder_agent_id: str = "",
    keep_recent_predecessors: int,
    timeout_s: float,
    wait_until_idle_s: float,
    poll_interval_s: float,
    select_active: bool = True,
    only_retired: bool = False,
    retired_agent_ids: list[str] | None = None,
) -> dict[str, Any]:
    import websockets

    active_aid = str(active_agent_id or "").strip()
    departing = str(departing_agent_id or "").strip()
    holder_aid = str(holder_agent_id or "").strip()
    archived: list[str] = []
    skipped: list[dict[str, Any]] = []
    eval_mid = 1
    async with websockets.connect(ws_url, max_size=2_000_000) as ws:
        raw_rows = await _hop._eval(ws, eval_mid, _list_root_sidebar_rows_js(root_id), timeout_s)
        eval_mid += 1
        if not isinstance(raw_rows, list):
            return {"ok": False, "reason": "list_rows_failed", "result": raw_rows}
        departing_sidebar_index: int | None = None
        row_by_aid: dict[str, dict[str, Any]] = {}
        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            aid = str(row.get("agent_id") or "").strip()
            if aid:
                row_by_aid[aid] = row
                if departing and aid == departing:
                    idx = row.get("sidebar_index")
                    if isinstance(idx, int):
                        departing_sidebar_index = idx
        if (
            select_active
            and active_aid
            and active_aid in row_by_aid
            and not row_by_aid[active_aid].get("active")
        ):
            await _hop._eval(ws, eval_mid, _select_agent_js(active_aid), timeout_s)
            eval_mid += 1
        predecessors: list[str] = []
        active_skip_done = False
        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            aid = str(row.get("agent_id") or "").strip()
            if not aid:
                continue
            if active_aid:
                if aid == active_aid:
                    continue
            elif row.get("active") and aid != departing and not active_skip_done:
                active_skip_done = True
                continue
            predecessors.append(aid)
        if any(int(row_by_aid.get(a, {}).get("created_at") or 0) for a in predecessors):
            indexed = list(enumerate(predecessors))
            indexed.sort(
                key=lambda t: (-int(row_by_aid.get(t[1], {}).get("created_at") or 0), t[0])
            )
            predecessors = [a for _, a in indexed]
        if departing:
            predecessors = [departing] + [a for a in predecessors if a != departing]
        kept_predecessors = predecessors[: max(0, keep_recent_predecessors)]
        protected = {active_aid, departing, holder_aid} - {""}
        retired_ids = set(retired_agent_ids or [])
        for aid in predecessors[max(0, keep_recent_predecessors) :]:
            if aid == holder_aid:
                skipped.append(
                    {"id": aid, "agent_id": aid, "ok": False, "reason": "lock_holder"}
                )
                continue
            if aid in protected:
                skipped.append(
                    {"id": aid, "agent_id": aid, "ok": False, "reason": "protected_agent_id"}
                )
                continue
            row = row_by_aid.get(aid, {})
            if only_retired:
                title = str(row.get("title") or "")
                if not title.startswith("[retired]") and aid not in retired_ids:
                    skipped.append(
                        {"id": aid, "agent_id": aid, "reason": "not_marked_retired"}
                    )
                    continue
            if row.get("unknown") is True:
                skipped.append(
                    {"id": aid, "agent_id": aid, "reason": "unknown_busy_state"}
                )
                continue
            if departing_sidebar_index is not None:
                idx = row.get("sidebar_index")
                if isinstance(idx, int) and idx < departing_sidebar_index:
                    skipped.append(
                        {
                            "agent_id": aid,
                            "ok": False,
                            "reason": "newer_than_departing",
                        }
                    )
                    continue
            if row.get("streaming") is True:
                skipped.append(
                    {
                        "agent_id": aid,
                        "ok": False,
                        "reason": "agent_streaming",
                        "queued": True,
                    }
                )
                continue
            archive_fn = getattr(_hop, "_archive_on_ws", _archive_on_ws)
            result = await archive_fn(
                ws,
                aid,
                timeout_s=timeout_s,
                wait_until_idle_s=wait_until_idle_s,
                poll_interval_s=poll_interval_s,
                eval_id=eval_mid,
            )
            eval_mid += 1
            if isinstance(result, dict) and result.get("ok"):
                archived.append(aid)
            else:
                skipped.append(
                    {"agent_id": aid, **(result if isinstance(result, dict) else {"result": result})}
                )
    return {
        "ok": True,
        "root_id": root_id,
        "kept_predecessors": kept_predecessors,
        "archived": archived,
        "skipped": skipped,
    }


import cursor_bridge.cursor_agents_hop as _hop  # noqa: E402
