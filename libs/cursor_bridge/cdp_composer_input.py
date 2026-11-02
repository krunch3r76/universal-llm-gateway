"""Page-scoped CDP Input.* composer insert/submit (no OS keyboard, no clipboard)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from cursor_bridge.dom_composer_paste import (
    INSTALL_OPERATOR_ACTIVITY_PROBE_JS,
    operator_activity_clear_suppress_js,
    operator_activity_suppress_js,
)

_INSERT_METHOD = "Input.insertText"
_KEYDOWN_METHOD = "Input.dispatchKeyEvent"


def _focus_composer_js(selector: str) -> str:
    sel = json.dumps(selector)
    return (
        f"(()=>{{const el=document.querySelector({sel});"
        "if(!el)return{ok:false,reason:'no_composer'};"
        "const prior=document.activeElement;"
        "const priorId=prior&&prior.id?prior.id:'';"
        "const priorTag=prior&&prior.tagName?prior.tagName:'';"
        "el.focus();"
        "return{ok:true,priorId,priorTag,priorIsComposer:prior===el};})()"
    )


def _restore_focus_js(selector: str, prior_id: str, prior_tag: str) -> str:
    sel = json.dumps(selector)
    pid = json.dumps(prior_id or "")
    ptag = json.dumps(prior_tag or "")
    return (
        f"(()=>{{const el=document.querySelector({sel});"
        f"const pid={pid}, ptag={ptag};"
        "let target=null;"
        "if(pid)target=document.getElementById(pid);"
        "if(!target&&ptag){"
        "const nodes=document.querySelectorAll(ptag);"
        "for(const n of nodes){if(n&&n!==el){target=n;break;}}"
        "}"
        "if(target&&typeof target.focus==='function')target.focus();"
        "return{ok:true};})()"
    )


def _mark_our_draft_sig_js(selector: str, line: str) -> str:
    sel = json.dumps(selector)
    msg = json.dumps(line)
    return (
        f"(()=>{{const g=window.__ulgOperatorActivity=window.__ulgOperatorActivity||{{}};"
        f"g.ourDraftSig={msg};"
        f"const el=document.querySelector({sel});"
        "return{ok:!!el};})()"
    )


def clear_our_composer_draft_js(
    *, selector: str, watermark: str, line: str = ""
) -> str:
    sel = json.dumps(selector)
    wm = json.dumps(watermark or "")
    sig = json.dumps((line or "").strip())
    return (
        f"(()=>{{const el=document.querySelector({sel});"
        "if(!el)return{ok:false,reason:'no_composer'};"
        "const draft=(el.innerText||'').trim();"
        f"const wm={wm}, sig={sig};"
        "const g=window.__ulgOperatorActivity=window.__ulgOperatorActivity||{};"
        "const ours=(sig&&draft===sig)||(wm&&draft.includes(wm));"
        "if(!ours)return{ok:true,cleared:false};"
        "if(el.editor&&el.editor.commands&&typeof el.editor.commands.setContent==='function'){"
        "try{el.editor.commands.setContent('');}catch(e){el.textContent='';}"
        "}else{el.textContent='';"
        "el.dispatchEvent(new InputEvent('input',{bubbles:true}));}"
        "g.ourDraftSig='';"
        "return{ok:true,cleared:true};})()"
    )


async def _cdp_response(ws: Any, mid: int) -> dict[str, Any]:
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            return msg


async def _runtime_eval(ws: Any, mid: int, expression: str) -> Any:
    await ws.send(
        json.dumps(
            {
                "id": mid,
                "method": "Runtime.evaluate",
                "params": {"expression": expression, "returnByValue": True},
            }
        )
    )
    raw = await asyncio.wait_for(_cdp_response(ws, mid), 8.0)
    if raw.get("error"):
        raise RuntimeError(raw["error"])
    return ((raw.get("result") or {}).get("result") or {}).get("value")


async def cdp_insert_text(
    ws_url: str, *, selector: str, text: str, restore_focus: bool = True
) -> dict[str, Any]:
    """Focus composer, insert via CDP Input.insertText, optionally restore focus."""
    import websockets

    async with websockets.connect(ws_url, max_size=2_000_000) as ws:
        await _runtime_eval(ws, 1, INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
        focus = await _runtime_eval(ws, 2, _focus_composer_js(selector))
        if not isinstance(focus, dict) or not focus.get("ok"):
            reason = (
                focus.get("reason") if isinstance(focus, dict) else "focus_failed"
            )
            return {"ok": False, "reason": reason or "focus_failed", "method": "cdp_insert"}
        await _runtime_eval(ws, 3, operator_activity_suppress_js())
        try:
            await ws.send(
                json.dumps(
                    {"id": 4, "method": _INSERT_METHOD, "params": {"text": text}}
                )
            )
            await asyncio.wait_for(_cdp_response(ws, 4), 8.0)
        finally:
            await _runtime_eval(ws, 5, operator_activity_clear_suppress_js())
        await _runtime_eval(ws, 6, _mark_our_draft_sig_js(selector, text))
        if restore_focus and not focus.get("priorIsComposer"):
            await _runtime_eval(
                ws,
                7,
                _restore_focus_js(
                    selector,
                    str(focus.get("priorId") or ""),
                    str(focus.get("priorTag") or ""),
                ),
            )
    return {"ok": True, "method": "cdp_insert"}


def _enter_preflight_js(*, selector: str, expected_agent_id: str, nonce_needle: str) -> str:
    sel = json.dumps(selector)
    exp = json.dumps(expected_agent_id or "")
    needle = json.dumps(nonce_needle or "")
    return (
        f"(()=>{{"
        f"const expected={exp}, needle={needle};"
        f"const g=window.__ulgOperatorActivity||{{}};"
        "const now=Date.now();"
        "const last=Number(g.lastInputMs||0);"
        "const idleMs=10000;"
        "const operatorActive=last>0&&(now-last)<idleMs;"
        "let activeAgentId='';"
        "const glassRows=Array.from(document.querySelectorAll('[data-perf-id=\"glass-sidebar-agent-row\"]'));"
        "const rows=glassRows.length?glassRows:Array.from(document.querySelectorAll('[role=listitem], [role=treeitem], [role=option]'));"
        "for(const el of rows){"
        "const aid=(el.getAttribute('data-sidebar-file-drop-agent-id')||el.getAttribute('data-agent-id')||'').trim();"
        "const trig=el.querySelector?el.querySelector('.ui-sidebar-menu-button-trigger[role=\"button\"]'):null;"
        "const selRow=el.getAttribute('data-active')==='true'||(trig&&trig.getAttribute('aria-current')==='page')||el.getAttribute('aria-selected')==='true';"
        "if(selRow&&aid){activeAgentId=aid;break;}"
        "}"
        f"const el=document.querySelector({sel});"
        "if(!el)return{ok:false,reason:'enter_no_composer'};"
        "if(!activeAgentId){activeAgentId=(el.getAttribute('data-sidebar-file-drop-editor-agent-id')||'').trim();}"
        "if(expected&&!activeAgentId){"
        "return{ok:false,reason:'enter_active_agent_unreadable'};"
        "}"
        "if(expected&&activeAgentId&&activeAgentId!==expected){"
        "return{ok:false,reason:'enter_wrong_active_agent',active_agent_id:activeAgentId};"
        "}"
        "const draft=(el.innerText||'').trim();"
        "if(needle&&!draft.includes(needle)){return{ok:false,reason:'enter_nonce_missing'};}"
        "const composerFocused=document.activeElement===el||el.contains(document.activeElement);"
        "if(operatorActive){"
        "if(!composerFocused){return{ok:false,reason:'enter_composer_not_focused_operator_active'};}"
        "return{ok:false,reason:'enter_operator_active'};"
        "}"
        "if(!composerFocused){el.focus();"
        "const after=document.activeElement===el||el.contains(document.activeElement);"
        "if(!after){return{ok:false,reason:'enter_composer_not_focused'};}"
        "}"
        "return{ok:true,composer_focused:composerFocused};"
        "})()"
    )


async def cdp_dispatch_enter(ws_url: str) -> dict[str, Any]:
    import websockets

    async with websockets.connect(ws_url, max_size=2_000_000) as ws:
        for mid, phase in ((1, "keyDown"), (2, "keyUp")):
            await ws.send(
                json.dumps(
                    {
                        "id": mid,
                        "method": _KEYDOWN_METHOD,
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
            await asyncio.wait_for(_cdp_response(ws, mid), 8.0)
    return {"ok": True, "method": "cdp_enter"}


async def cdp_dispatch_enter_guarded(
    ws_url: str,
    *,
    selector: str,
    expected_agent_id: str,
    nonce_needle: str,
) -> dict[str, Any]:
    """Preflight composer/agent/nonce, in-page focus if idle, then CDP Enter."""
    import websockets

    if not str(nonce_needle or "").strip():
        return {
            "ok": False,
            "reason": "enter_nonce_empty",
            "method": "cdp_enter",
        }

    async with websockets.connect(ws_url, max_size=2_000_000) as ws:
        pre = await _runtime_eval(
            ws,
            1,
            _enter_preflight_js(
                selector=selector,
                expected_agent_id=expected_agent_id,
                nonce_needle=nonce_needle,
            ),
        )
        if not isinstance(pre, dict) or not pre.get("ok"):
            reason = pre.get("reason") if isinstance(pre, dict) else "enter_preflight_failed"
            return {
                "ok": False,
                "reason": str(reason or "enter_preflight_failed"),
                "method": "cdp_enter",
            }
        for mid, phase in ((2, "keyDown"), (3, "keyUp")):
            await ws.send(
                json.dumps(
                    {
                        "id": mid,
                        "method": _KEYDOWN_METHOD,
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
            await asyncio.wait_for(_cdp_response(ws, mid), 8.0)
    return {"ok": True, "method": "cdp_enter", "composer_focused": pre.get("composer_focused")}


async def clear_our_composer_draft(
    ws_url: str, *, selector: str, watermark: str, line: str = ""
) -> dict[str, Any]:
    from cursor_bridge.dom_composer_paste import _cdp_eval

    raw = await _cdp_eval(
        ws_url, clear_our_composer_draft_js(selector=selector, watermark=watermark, line=line)
    )
    return raw if isinstance(raw, dict) else {"ok": False}


__all__ = [
    "cdp_dispatch_enter",
    "cdp_dispatch_enter_guarded",
    "cdp_insert_text",
    "clear_our_composer_draft",
    "clear_our_composer_draft_js",
]
