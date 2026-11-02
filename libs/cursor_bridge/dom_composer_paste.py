"""DOM-only composer insert and send (no focus steal, no Input.* CDP paths)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

_EXPECT_DRAFT_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

GROK_PROMPT_SELECTOR = '[aria-label="Prompt"][contenteditable="true"]'
OPERATOR_PROBE_VERSION = 2
_STALE_WAKE_DRAFT = re.compile(r"^BRIDGE_WAKE\s+thread=\S+\s+turn=\d+(?:,\d+)*$")


def _is_stale_wake_draft(draft: str) -> bool:
    return bool(_STALE_WAKE_DRAFT.match(str(draft or "").strip()))


def composer_draft_sha256_hex(draft: str) -> str:
    """UTF-8 SHA-256 hex digest of composer ``innerText`` (same bytes the probe reads)."""
    return hashlib.sha256(str(draft).encode("utf-8")).hexdigest()


def probe_foreign_draft(
    probe: dict[str, Any],
    *,
    expect_draft_sha256: str | None = None,
) -> bool:
    """True when the probe reports a non-agent draft (after optional SHA expectation)."""
    foreign = bool(probe.get("foreign_draft"))
    if not foreign:
        return False
    expect = str(expect_draft_sha256 or "").strip().lower()
    if not expect or not _EXPECT_DRAFT_SHA256_RE.match(expect):
        return foreign
    draft = str(probe.get("composer_draft") or "")
    if draft and composer_draft_sha256_hex(draft) == expect:
        return False
    return True


INSTALL_OPERATOR_ACTIVITY_PROBE_JS = rf"""
(() => {{
  const PROBE_VERSION = {OPERATOR_PROBE_VERSION};
  const g = window.__ulgOperatorActivity = window.__ulgOperatorActivity || {{
    lastInputMs: 0,
    ourDraftSig: '',
    installed: false,
    probeVersion: 0,
  }};
  if (g.probeVersion === PROBE_VERSION && g.installed) {{
    return {{ok: true, already: true, probeVersion: PROBE_VERSION}};
  }}
  if (g._bump) {{
    window.removeEventListener('keydown', g._bump, true);
    window.removeEventListener('input', g._bump, true);
    window.removeEventListener('pointerdown', g._bump, true);
  }}
  g._bump = (e) => {{
    if (!e || !e.isTrusted) return;
    if (Date.now() < Number(g.suppressUntilMs || 0)) return;
    g.lastInputMs = Date.now();
  }};
  window.addEventListener('keydown', g._bump, true);
  window.addEventListener('input', g._bump, true);
  window.addEventListener('pointerdown', g._bump, true);
  g.installed = true;
  g.probeVersion = PROBE_VERSION;
  return {{ok: true, installed: true, probeVersion: PROBE_VERSION}};
}})()
"""

READ_OPERATOR_BUSY_JS = r"""
(() => {
  const g = window.__ulgOperatorActivity || {};
  const now = Date.now();
  const last = Number(g.lastInputMs || 0);
  const idleMs = 10000;
  const operatorActive = last > 0 && (now - last) < idleMs;
  const sels = %SELECTORS%;
  let el = null;
  for (const s of sels) {
    el = document.querySelector(s);
    if (el) break;
  }
  const draft = el ? (el.innerText || '').trim() : '';
  const sig = String(g.ourDraftSig || '').trim();
  const staleWake = /^BRIDGE_WAKE\s+thread=\S+\s+turn=\d+(?:,\d+)*$/.test(draft);
  const watermark = %WATERMARK%;
  const ours = sig && draft === sig;
  const foreignDraft = draft.length > 0 && !staleWake && !ours
    && !(watermark && draft.includes(watermark));
  return {
    ok: true,
    operator_active: operatorActive,
    foreign_draft: foreignDraft,
    composer_draft: draft,
    document_has_focus: document.hasFocus(),
    document_visibility: document.visibilityState || '',
    draft_len: draft.length,
  };
})()
"""

_SEND_BTN_SELECTORS = (
    'button[aria-label="Send message"]:not([disabled])',
    'button[aria-label="Send"]:not([disabled])',
    'button[data-testid="send-button"]:not([disabled])',
    'button[type="submit"]:not([disabled])',
)


def composer_selector(*, app: str, agent_id: str = "") -> str:
    aid = str(agent_id or "").strip()
    if app == "cursor" and aid:
        return (
            '[aria-label="Prompt"][contenteditable="true"]'
            f'[data-sidebar-file-drop-editor-agent-id="{aid}"]'
        )
    return GROK_PROMPT_SELECTOR


def _flatten_paste_line(text: str) -> str:
    return " ".join((text or "").splitlines()).strip()


def _dom_paste_text(text: str, *, preserve_newlines: bool) -> str:
    if preserve_newlines:
        return (text or "").strip()
    return _flatten_paste_line(text)


def _selector_list(selector: str) -> list[str]:
    return [s.strip() for s in selector.split(",") if s.strip()]


def _find_composer_js(selector: str) -> str:
    sels = json.dumps(_selector_list(selector) or [selector])
    return f"(()=>{{const sels={sels};for(const s of sels){{const el=document.querySelector(s);if(el)return el;}}return null;}})()"


def _set_composer_body(selector: str, text: str, *, preserve_newlines: bool = False) -> str:
    find = _find_composer_js(selector)
    line = _dom_paste_text(text, preserve_newlines=preserve_newlines)
    msg = json.dumps(line)
    return (
        f"(()=>{{const el=({find});"
        "if(!el)return{ok:false,reason:'no_composer'};"
        f"const line={msg};"
        "const draft=(el.innerText||el.value||'').trim();"
        "const stale=/^BRIDGE_WAKE\\s+thread=\\S+\\s+turn=\\d+(?:,\\d+)*$/.test(draft);"
        "if(draft.length>0&&!stale&&draft!==line.trim())"
        "return{ok:false,reason:'composer_not_empty',draft};"
        "const g=window.__ulgOperatorActivity=window.__ulgOperatorActivity||{};"
        "if(el.editor&&el.editor.commands&&typeof el.editor.commands.setContent==='function'){"
        "try{el.editor.commands.setContent(line);}catch(e){"
        "el.textContent=line;el.dispatchEvent(new InputEvent('input',{bubbles:true,data:line}));}"
        "}else if(el.tagName==='TEXTAREA'||el.tagName==='INPUT'){"
        "el.value=line;el.dispatchEvent(new Event('input',{bubbles:true}));"
        "}else{"
        "el.textContent=line;"
        "el.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:line}));"
        "}"
        "const after=(el.innerText||el.value||'').trim();"
        "g.ourDraftSig=after;"
        "return{ok:true,sig:after};})()"
    )


def _click_send_own_draft_body(selector: str) -> str:
    """Send only when composer draft matches this writer's ourDraftSig."""
    find = _find_composer_js(selector)
    btn_sels = json.dumps(list(_SEND_BTN_SELECTORS))
    return (
        f"(()=>{{const el=({find});"
        "if(!el)return{ok:false,reason:'no_composer',send_clicked:false};"
        "const g=window.__ulgOperatorActivity=window.__ulgOperatorActivity||{};"
        "const sig=String(g.ourDraftSig||'').trim();"
        "const draft=(el.innerText||el.value||'').trim();"
        "if(!sig||draft!==sig)"
        "return{ok:false,reason:'draft_not_ours',send_clicked:false,draft,sig};"
        f"const root=el.closest('form')||el.parentElement||document;"
        f"const btnSels={btn_sels};"
        "let btn=null;"
        "for(const sel of btnSels){btn=root.querySelector(sel);if(btn)break;}"
        "if(!btn)return{ok:false,reason:'no_send_button',send_clicked:false};"
        "btn.click();"
        "g.ourDraftSig='';"
        "return{ok:true,send_clicked:true};})()"
    )


def clear_composer_draft_dom_no_focus_js(selector: str) -> str:
    """Clear composer text via value setter + input event; no focus(), no Input.*."""
    find = _find_composer_js(selector)
    return (
        f"(()=>{{const el=({find});"
        "if(!el)return{ok:false,reason:'no_composer'};"
        "const g=window.__ulgOperatorActivity=window.__ulgOperatorActivity||{};"
        "const now=Date.now();"
        "const last=Number(g.lastInputMs||0);"
        "const operatorActive=last>0&&(now-last)<10000;"
        "if(operatorActive||document.hasFocus())"
        "return{ok:false,reason:'operator_active',operator_active:operatorActive,"
        "document_has_focus:document.hasFocus()};"
        "if(el.editor&&el.editor.commands&&typeof el.editor.commands.setContent==='function'){"
        "try{el.editor.commands.setContent('');}catch(e){el.textContent='';}"
        "}else if(el.tagName==='TEXTAREA'||el.tagName==='INPUT'){"
        "const desc=Object.getOwnPropertyDescriptor("
        "el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value');"
        "if(desc&&desc.set)desc.set.call(el,'');else el.value='';"
        "el.dispatchEvent(new Event('input',{bubbles:true}));"
        "}else{"
        "el.textContent='';"
        "el.dispatchEvent(new InputEvent('input',{bubbles:true}));"
        "}"
        "g.ourDraftSig='';"
        "const after=(el.innerText||el.value||'').trim();"
        "return{ok:true,cleared:after.length===0,draft:after};})()"
    )


async def clear_composer_draft_dom_no_focus(ws_url: str, *, selector: str) -> dict[str, Any]:
    raw = await _cdp_eval(ws_url, clear_composer_draft_dom_no_focus_js(selector))
    return raw if isinstance(raw, dict) else {"ok": False}


def _click_send_body(
    selector: str, line: str, *, preserve_newlines: bool = False
) -> str:
    find = _find_composer_js(selector)
    lj = json.dumps(_dom_paste_text(line, preserve_newlines=preserve_newlines))
    btn_sels = json.dumps(list(_SEND_BTN_SELECTORS))
    return (
        f"(()=>{{const el=({find});"
        "if(!el)return{ok:false,reason:'no_composer',send_clicked:false};"
        "const g=window.__ulgOperatorActivity=window.__ulgOperatorActivity||{};"
        f"const text=(el.innerText||el.value||'').trim(),line={lj};"
        "if(!text||(text!==line.trim()&&!/^BRIDGE_WAKE\\s+thread=\\S+\\s+turn=\\d+(?:,\\d+)*$/.test(text)))"
        "return{ok:false,reason:'composer_text_mismatch',send_clicked:false};"
        f"const root=el.closest('form')||el.parentElement||document;"
        f"const btnSels={btn_sels};"
        "let btn=null;"
        "for(const sel of btnSels){btn=root.querySelector(sel);if(btn)break;}"
        "if(!btn)return{ok:false,reason:'no_send_button',send_clicked:false};"
        "btn.click();"
        "g.ourDraftSig='';"
        "return{ok:true,send_clicked:true};})()"
    )


def operator_busy_probe_js(*, selector: str, watermark: str = "") -> str:
    sels = _selector_list(selector) or [selector]
    body = READ_OPERATOR_BUSY_JS.replace("%SELECTORS%", json.dumps(sels))
    return body.replace("%WATERMARK%", json.dumps(watermark or ""))


def operator_activity_suppress_js(*, duration_ms: int = 5000) -> str:
    """Raise suppress window so trusted CDP Input.* does not mark operator active."""
    return (
        "(()=>{const g=window.__ulgOperatorActivity="
        f"window.__ulgOperatorActivity||{{}};g.suppressUntilMs=Date.now()+{int(duration_ms)};"
        "return{ok:true};})()"
    )


def operator_activity_clear_suppress_js() -> str:
    return (
        "(()=>{const g=window.__ulgOperatorActivity=window.__ulgOperatorActivity||{};"
        "g.suppressUntilMs=0;return{ok:true};})()"
    )


async def _cdp_eval(
    ws_url: str, expression: str, *, await_promise: bool = False, timeout_s: float = 8.0
) -> Any:
    import websockets

    async with websockets.connect(ws_url, max_size=2_000_000) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "awaitPromise": await_promise,
                        "returnByValue": True,
                    },
                }
            )
        )
        raw = await asyncio.wait_for(_cdp_response(ws, 1), timeout_s)
    if raw.get("error"):
        raise RuntimeError(raw["error"])
    return ((raw.get("result") or {}).get("result") or {}).get("value")


async def _cdp_response(ws: Any, mid: int) -> dict[str, Any]:
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            return msg


async def install_operator_activity_probe(ws_url: str) -> None:
    await _cdp_eval(ws_url, INSTALL_OPERATOR_ACTIVITY_PROBE_JS)


async def read_operator_busy(
    ws_url: str, *, selector: str, watermark: str = ""
) -> dict[str, Any]:
    raw = await _cdp_eval(
        ws_url, operator_busy_probe_js(selector=selector, watermark=watermark)
    )
    return raw if isinstance(raw, dict) else {"ok": False}


async def wait_for_operator_idle(
    ws_url: str,
    *,
    selector: str,
    watermark: str = "",
    max_wait_s: float = 30.0,
    idle_ms: int = 10_000,
    poll_s: float = 0.5,
    refuse_if_active: bool = False,
    refuse_if_foreign_draft: bool = False,
    expect_draft_sha256: str | None = None,
) -> dict[str, Any]:
    """Backoff until operator idle and no foreign draft, or timeout."""
    await install_operator_activity_probe(ws_url)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_wait_s
    delay = poll_s
    while loop.time() <= deadline:
        probe = await read_operator_busy(ws_url, selector=selector, watermark=watermark)
        active = bool(probe.get("operator_active"))
        foreign = probe_foreign_draft(
            probe, expect_draft_sha256=expect_draft_sha256
        )
        if active and refuse_if_active:
            return {"ok": False, "reason": "operator_busy", "idle": False}
        if foreign and refuse_if_foreign_draft:
            return {"ok": False, "reason": "operator_busy", "idle": False}
        if not active and not foreign:
            return {"ok": True, "idle": True}
        remaining = max(0.0, deadline - loop.time())
        await asyncio.sleep(min(delay, remaining))
        delay = min(delay * 1.5, 4.0)
    return {"ok": False, "reason": "operator_busy", "idle": False}


async def dom_set_composer_text(
    ws_url: str, *, selector: str, text: str, preserve_newlines: bool = False
) -> dict[str, Any]:
    value = await _cdp_eval(
        ws_url, _set_composer_body(selector, text, preserve_newlines=preserve_newlines)
    )
    if not isinstance(value, dict):
        return {"ok": False, "reason": "paste_failed"}
    if not value.get("ok"):
        return value
    return {"ok": True}


async def dom_click_send(
    ws_url: str, *, selector: str, line: str, preserve_newlines: bool = False
) -> dict[str, Any]:
    value = await _cdp_eval(
        ws_url,
        _click_send_body(selector, line, preserve_newlines=preserve_newlines),
    )
    if not isinstance(value, dict):
        return {"ok": False, "send_clicked": False}
    return value


async def dom_click_send_own_draft(ws_url: str, *, selector: str) -> dict[str, Any]:
    """Click send when composer text equals ourDraftSig (submit-without-text path)."""
    value = await _cdp_eval(ws_url, _click_send_own_draft_body(selector))
    if not isinstance(value, dict):
        return {"ok": False, "send_clicked": False}
    return value


async def dom_insert_and_send(
    ws_url: str, *, selector: str, line: str, preserve_newlines: bool = False
) -> dict[str, Any]:
    """Insert once via DOM; send via send-button click (no Enter, no focus())."""
    ins = await dom_set_composer_text(
        ws_url, selector=selector, text=line, preserve_newlines=preserve_newlines
    )
    if not ins.get("ok"):
        return ins
    send = await dom_click_send(
        ws_url, selector=selector, line=line, preserve_newlines=preserve_newlines
    )
    if not send.get("ok"):
        return {
            "ok": False,
            "reason": str(send.get("reason") or "send_failed"),
            "send_fallback_clicked": False,
        }
    return {"ok": True, "send_fallback_clicked": bool(send.get("send_clicked"))}


def foreign_draft_guard(surface: dict[str, Any], *, watermark: str = "") -> str | None:
    draft = str(surface.get("composer_draft") or "").strip()
    if not draft or _is_stale_wake_draft(draft):
        return None
    if watermark and watermark in draft:
        return None
    return "composer_not_empty"


__all__ = [
    "GROK_PROMPT_SELECTOR",
    "OPERATOR_PROBE_VERSION",
    "composer_draft_sha256_hex",
    "composer_selector",
    "probe_foreign_draft",
    "dom_click_send",
    "dom_click_send_own_draft",
    "dom_insert_and_send",
    "dom_set_composer_text",
    "foreign_draft_guard",
    "install_operator_activity_probe",
    "operator_activity_clear_suppress_js",
    "operator_activity_suppress_js",
    "operator_busy_probe_js",
    "read_operator_busy",
    "wait_for_operator_idle",
]
