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
        "g.ourDraftWriter='';"
        f"const el=document.querySelector({sel});"
        "return{ok:!!el};})()"
    )


def clear_our_composer_draft_js(
    *, selector: str, watermark: str, line: str = ""
) -> str:
    """JS that clears a composer draft this writer owns and drops its sig.

    Matches the draft against ``line`` or ``watermark``, empties the node, then
    sets ``ourDraftSig`` and ``ourDraftWriter`` to empty so a later submit
    cannot treat the cleared text as ours.
    """
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
        "g.ourDraftWriter='';"
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


_PRE_INPUT_REFUSAL_REASONS = frozenset(
    {
        "url_mismatch",
        "no_composer",
        "ambiguous_work_composer",
        "work_native_unsupported",
        "work_dom_api_available",
        "composer_not_empty",
        "operator_active",
        "competing_text_control",
        "focus_failed",
        "foreign_attachment",
        "selection_active",
        "editor_changed",
    }
)

_WORK_ATTACHMENT_JS = r"""
function ulgAttachmentTextIsFile(text) {
  const t = String(text || '').replace(/\s+/g, ' ').trim();
  if (!t) return false;
  const low = t.toLowerCase();
  if (
    low === 'attach' || low === 'attachment' || low === 'add'
    || low === 'add files' || low === 'upload'
  ) return false;
  return /\.[a-z0-9]{1,8}\b/i.test(t);
}
function ulgIsRemoveControl(node) {
  if (!node || typeof node.getAttribute !== 'function') return false;
  const label = String(node.getAttribute('aria-label') || '');
  const testid = String(node.getAttribute('data-testid') || '');
  const cls = String(node.getAttribute('class') || '');
  return /remove|dismiss/i.test(label) || /remove|dismiss/i.test(testid) || /remove/i.test(cls);
}
function ulgNodeIsPopulatedFile(node) {
  if (!node) return false;
  if (ulgIsRemoveControl(node)) return true;
  if (typeof node.querySelector === 'function') {
    const fileInput = node.querySelector('input[type="file"]');
    if (fileInput && fileInput.files && fileInput.files.length > 0) return true;
    if (node.querySelector(
      '[aria-label*="remove" i],[aria-label*="dismiss" i],[data-testid*="remove" i]'
    )) return true;
  }
  return ulgAttachmentTextIsFile(node.innerText || '');
}
function ulgEditorFooterRegion(el) {
  let region = (el && el.parentElement) ? el.parentElement : el;
  let cur = el;
  for (let step = 0; cur && step < 8; step += 1) {
    const parent = cur.parentElement;
    if (!parent || parent === document.body || parent === document.documentElement) break;
    cur = parent;
    region = cur;
    if (typeof cur.querySelector === 'function' && cur.querySelector(
      '.ComposerLayoutFooter,[class*="Footer"],[class*="footer" i],button[aria-label*="Send" i],button[aria-label*="Stop" i]'
    )) return cur;
  }
  return region;
}
function ulgHasPopulatedAttachment(el) {
  const region = ulgEditorFooterRegion(el);
  if (!region || region === document.body || region === document.documentElement) return false;
  if (typeof region.querySelectorAll !== 'function') return false;
  const nodes = region.querySelectorAll(
    '[data-testid*="attachment" i],.file-chip,[class*="file-chip" i],[class*="attachment" i]'
  );
  for (const node of nodes) {
    if (!node || node === el) continue;
    if (ulgNodeIsPopulatedFile(node)) return true;
  }
  const controls = region.querySelectorAll('button,[role="button"],input[type="file"]');
  for (const node of controls) {
    if (!node || node === el) continue;
    const tag = String(node.tagName || '');
    if (tag === 'INPUT') {
      const type = String(node.getAttribute('type') || '').toLowerCase();
      if (type === 'file' && node.files && node.files.length > 0) return true;
      continue;
    }
    if (ulgIsRemoveControl(node)) return true;
  }
  return false;
}
"""


def _work_native_preflight_js(
    *, expected_url: str, text: str, draft_writer: str, preserve_newlines: bool
) -> str:
    """Build page JS that validates Work editor identity before native Input.insertText."""
    from cursor_bridge.dom_composer_paste import (
        _ULG_NORM_COMPOSER_JS,
        _WORK_EDITOR_RESOLVE_JS,
        _flatten_paste_line,
    )

    exp = json.dumps(str(expected_url or "").split("?")[0])
    line = json.dumps(
        text if preserve_newlines else _flatten_paste_line(text)
    )
    writer = json.dumps(str(draft_writer or ""))
    return (
        f"(()=>{{{_ULG_NORM_COMPOSER_JS}{_WORK_EDITOR_RESOLVE_JS}{_WORK_ATTACHMENT_JS}"
        f"const expectUrl={exp};"
        f"const expectLine={line};"
        f"const expectWriter={writer};"
        "const here=String(location.href||'').split('?')[0];"
        "if(expectUrl&&here!==expectUrl)return{ok:false,reason:'url_mismatch'};"
        "const resolved=ulgResolveVisibleWorkComposer();"
        "if(!resolved.ok)return{ok:false,reason:resolved.reason||'no_composer'};"
        "const el=resolved.el;"
        "if(ulgIsHiddenWorkHit(el))return{ok:false,reason:'no_composer'};"
        "if(el.editor&&el.editor.commands&&typeof el.editor.commands.setContent==='function'){"
        "return{ok:false,reason:'work_dom_api_available'};}"
        "if(!el.pmViewDesc)return{ok:false,reason:'work_native_unsupported'};"
        "if(!ulgComposerDomEmpty(el)){"
        "return{ok:false,reason:'composer_not_empty'};}"
        "const g=window.__ulgOperatorActivity=window.__ulgOperatorActivity||{};"
        "const now=Date.now();"
        "const last=Number(g.lastInputMs||0);"
        "if(last>0&&(now-last)<10000)return{ok:false,reason:'operator_active'};"
        "const active=document.activeElement;"
        "if(active&&active!==el&&!el.contains(active)){"
        "const tag=active.tagName||'';"
        "if(tag==='INPUT'||tag==='TEXTAREA'||(active.isContentEditable&&active!==el)){"
        "return{ok:false,reason:'competing_text_control'};}}"
        "if(ulgHasPopulatedAttachment(el))return{ok:false,reason:'foreign_attachment'};"
        "const sel=window.getSelection&&window.getSelection();"
        "if(sel&&!sel.isCollapsed)return{ok:false,reason:'selection_active'};"
        "el.focus();"
        "const focused=document.activeElement===el||el.contains(document.activeElement);"
        "if(!focused)return{ok:false,reason:'focus_failed'};"
        "const hereAfter=String(location.href||'').split('?')[0];"
        "if(expectUrl&&hereAfter!==expectUrl)return{ok:false,reason:'url_mismatch'};"
        "const resolvedAfter=ulgResolveVisibleWorkComposer();"
        "if(!resolvedAfter.ok||resolvedAfter.el!==el)return{ok:false,reason:'editor_changed'};"
        "window.__ulgWorkNativeEditor=el;"
        "window.__ulgWorkNativeEditorId=String(el.getAttribute('data-ulg-native-id')||'work-0');"
        "return{ok:true,expectLine,expectWriter,editorId:window.__ulgWorkNativeEditorId};})()"
    )


def _work_native_insert_gate_js(
    *, expected_url: str, text: str, draft_writer: str, preserve_newlines: bool
) -> str:
    """Build the last page eval before Input.insertText.

    Rechecks the pinned visible Work editor, exact URL, focus, operator idle,
    emptiness, and populated attachments. Suppress is armed only after those
    checks, in this same script, so no later unguarded eval runs before insert.
    ``text`` and ``draft_writer`` stay in the closure for the caller contract;
    this gate does not write them.
    """
    from cursor_bridge.dom_composer_paste import (
        _ULG_NORM_COMPOSER_JS,
        _WORK_EDITOR_RESOLVE_JS,
        _flatten_paste_line,
    )

    exp = json.dumps(str(expected_url or "").split("?")[0])
    line = json.dumps(text if preserve_newlines else _flatten_paste_line(text))
    writer = json.dumps(str(draft_writer or ""))
    return (
        f"(()=>{{{_ULG_NORM_COMPOSER_JS}{_WORK_EDITOR_RESOLVE_JS}{_WORK_ATTACHMENT_JS}"
        f"const expectUrl={exp};"
        f"const expectLine={line};"
        f"const expectWriter={writer};"
        "const pinned=window.__ulgWorkNativeEditor;"
        "const here=String(location.href||'').split('?')[0];"
        "if(expectUrl&&here!==expectUrl)return{ok:false,reason:'url_mismatch'};"
        "const resolved=ulgResolveVisibleWorkComposer();"
        "if(!resolved.ok)return{ok:false,reason:resolved.reason||'no_composer'};"
        "const el=resolved.el;"
        "if(ulgIsHiddenWorkHit(el))return{ok:false,reason:'no_composer'};"
        "if(!pinned||pinned!==el)return{ok:false,reason:'editor_changed'};"
        "if(el.editor&&el.editor.commands&&typeof el.editor.commands.setContent==='function'){"
        "return{ok:false,reason:'work_dom_api_available'};}"
        "if(!el.pmViewDesc)return{ok:false,reason:'work_native_unsupported'};"
        "if(!ulgComposerDomEmpty(el))return{ok:false,reason:'composer_not_empty'};"
        "const g=window.__ulgOperatorActivity=window.__ulgOperatorActivity||{};"
        "const now=Date.now();"
        "const last=Number(g.lastInputMs||0);"
        "if(last>0&&(now-last)<10000)return{ok:false,reason:'operator_active'};"
        "if(ulgHasPopulatedAttachment(el))return{ok:false,reason:'foreign_attachment'};"
        "const sel=window.getSelection&&window.getSelection();"
        "if(sel&&!sel.isCollapsed)return{ok:false,reason:'selection_active'};"
        "const active=document.activeElement;"
        "const focused=active===el||(el.contains&&el.contains(active));"
        "if(!focused)return{ok:false,reason:'focus_failed'};"
        "const hereAfter=String(location.href||'').split('?')[0];"
        "if(expectUrl&&hereAfter!==expectUrl)return{ok:false,reason:'url_mismatch'};"
        "const resolvedAfter=ulgResolveVisibleWorkComposer();"
        "if(!resolvedAfter.ok||resolvedAfter.el!==el||ulgIsHiddenWorkHit(resolvedAfter.el)){"
        "return{ok:false,reason:'editor_changed'};}"
        "const activeAfter=document.activeElement;"
        "if(!(activeAfter===el||(el.contains&&el.contains(activeAfter)))){"
        "return{ok:false,reason:'focus_failed'};}"
        "g.suppressUntilMs=Date.now()+5000;"
        "return{ok:true,expectLine,expectWriter};})()"
    )


def _work_native_readback_js(
    *,
    text: str,
    draft_writer: str,
    preserve_newlines: bool,
    expected_url: str,
) -> str:
    """Build page JS that re-validates editor/URL and assigns draft ownership after insert."""
    from cursor_bridge.dom_composer_paste import (
        _ULG_NORM_COMPOSER_JS,
        _WORK_EDITOR_RESOLVE_JS,
        _flatten_paste_line,
    )

    line = json.dumps(
        text if preserve_newlines else _flatten_paste_line(text)
    )
    writer = json.dumps(str(draft_writer or ""))
    exp = json.dumps(str(expected_url or "").split("?")[0])
    return (
        f"(()=>{{{_ULG_NORM_COMPOSER_JS}{_WORK_EDITOR_RESOLVE_JS}"
        f"const expectLine={line};"
        f"const expectWriter={writer};"
        f"const expectUrl={exp};"
        "const here=String(location.href||'').split('?')[0];"
        "if(expectUrl&&here!==expectUrl)return{ok:false,reason:'url_mismatch'};"
        "const resolved=ulgResolveVisibleWorkComposer();"
        "if(!resolved.ok)return{ok:false,reason:resolved.reason||'no_composer'};"
        "const el=resolved.el;"
        "if(ulgIsHiddenWorkHit(el))return{ok:false,reason:'no_composer'};"
        "const pinned=window.__ulgWorkNativeEditor;"
        "const editorId=String(window.__ulgWorkNativeEditorId||'');"
        "if(!pinned||pinned!==el)return{ok:false,reason:'editor_changed'};"
        "const normDraft=ulgNormalizeComposerText(el.innerText||'');"
        "const normLine=ulgNormalizeComposerText(expectLine);"
        "if(normDraft!==normLine)return{ok:false,reason:'composer_text_mismatch'};"
        "const g=window.__ulgOperatorActivity=window.__ulgOperatorActivity||{};"
        "g.ourDraftSig=normDraft;"
        "g.ourDraftWriter=expectWriter;"
        "return{ok:true,sig:normDraft,phase:'inserted',inserted:true,pasted:true,editorId};})()"
    )


async def cdp_work_native_insert_text(
    ws_url: str,
    *,
    text: str,
    expected_url: str,
    draft_writer: str,
    preserve_newlines: bool = False,
) -> dict[str, Any]:
    """Insert text once into a hydrated Work composer after a final page gate.

    The gate is the last Runtime.evaluate before Input.insertText. It rechecks
    the same visible editor, URL, focus, operator idle, emptiness, and
    attachments, and arms input suppression only inside that script. A failed
    gate does not insert. Uncertainty after the insert command is fenced.
    """
    import websockets

    uncertain = {
        "ok": False,
        "insertion_unknown": True,
        "pasted": False,
        "inserted": False,
        "delivered": False,
        "retry_eligible": False,
    }
    async with websockets.connect(ws_url, max_size=2_000_000) as ws:
        await _runtime_eval(ws, 1, INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
        pre = await _runtime_eval(
            ws,
            2,
            _work_native_preflight_js(
                expected_url=expected_url,
                text=text,
                draft_writer=draft_writer,
                preserve_newlines=preserve_newlines,
            ),
        )
        if not isinstance(pre, dict) or not pre.get("ok"):
            reason = pre.get("reason") if isinstance(pre, dict) else "preflight_failed"
            reason_s = str(reason or "preflight_failed")
            if reason_s in _PRE_INPUT_REFUSAL_REASONS:
                return {
                    "ok": False,
                    "reason": reason_s,
                    "pasted": False,
                    "inserted": False,
                    "phase": "insertion_failed",
                }
            return {**uncertain, "reason": reason_s, "phase": "insertion_unknown"}
        try:
            gate = await _runtime_eval(
                ws,
                3,
                _work_native_insert_gate_js(
                    expected_url=expected_url,
                    text=text,
                    draft_writer=draft_writer,
                    preserve_newlines=preserve_newlines,
                ),
            )
        except TimeoutError:
            return {
                "ok": False,
                "reason": "insert_gate_timeout",
                "pasted": False,
                "inserted": False,
                "phase": "insertion_failed",
            }
        except Exception:
            return {
                "ok": False,
                "reason": "insert_gate_failed",
                "pasted": False,
                "inserted": False,
                "phase": "insertion_failed",
            }
        if not isinstance(gate, dict) or not gate.get("ok"):
            reason = gate.get("reason") if isinstance(gate, dict) else "insert_gate_failed"
            reason_s = str(reason or "insert_gate_failed")
            return {
                "ok": False,
                "reason": reason_s,
                "pasted": False,
                "inserted": False,
                "phase": "insertion_failed",
            }
        insert_dispatched = False
        try:
            await ws.send(
                json.dumps(
                    {
                        "id": 4,
                        "method": _INSERT_METHOD,
                        "params": {
                            "text": text
                            if preserve_newlines
                            else " ".join(text.splitlines()).strip()
                        },
                    }
                )
            )
            insert_resp = await asyncio.wait_for(_cdp_response(ws, 4), 8.0)
            if insert_resp.get("error"):
                return {
                    **uncertain,
                    "reason": "native_insert_error",
                    "phase": "insertion_unknown",
                }
            insert_dispatched = True
        except TimeoutError:
            return {**uncertain, "reason": "native_insert_timeout", "phase": "insertion_unknown"}
        except Exception:
            return {**uncertain, "reason": "native_insert_transport", "phase": "insertion_unknown"}
        finally:
            try:
                await _runtime_eval(ws, 5, operator_activity_clear_suppress_js())
            except Exception:
                if insert_dispatched:
                    return {
                        **uncertain,
                        "reason": "native_insert_cleanup",
                        "phase": "insertion_unknown",
                    }
        try:
            rb = await _runtime_eval(
                ws,
                6,
                _work_native_readback_js(
                    text=text,
                    draft_writer=draft_writer,
                    preserve_newlines=preserve_newlines,
                    expected_url=expected_url,
                ),
            )
        except TimeoutError:
            return {**uncertain, "reason": "readback_timeout", "phase": "insertion_unknown"}
        except Exception:
            return {**uncertain, "reason": "readback_transport", "phase": "insertion_unknown"}
        if not isinstance(rb, dict) or not rb.get("ok"):
            reason = rb.get("reason") if isinstance(rb, dict) else "readback_failed"
            return {
                **uncertain,
                "reason": str(reason or "composer_text_mismatch"),
                "phase": "insertion_unknown",
            }
    return rb


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
        try:
            await _runtime_eval(ws, 3, operator_activity_suppress_js())
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
    """Preflight the composer, then dispatch Enter and record that fact.

    Callers are the operator-paste Enter fallback. After the preflight
    accepts the agent and nonce, and before keyDown, this records the same
    dispatch fact as a send click so an unverified delivery is not retried.
    A preflight refusal returns before any key and does not record the fact.
    Returns ok with method ``cdp_enter`` when both key events complete, or
    ok false with a reason when preflight refuses.
    """
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
        from cursor_bridge.dom_composer_paste import _mark_click_dispatched

        _mark_click_dispatched()
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
    "cdp_work_native_insert_text",
    "clear_our_composer_draft",
    "clear_our_composer_draft_js",
]
