"""DOM-only composer insert and send for the cursor bridge (no focus steal, no Input.*).

Callers are the insert and send wrappers in this package. Send uses two CDP
evaluates on one page clock: a synchronous permit stamp, then the click script.
Task cancel cannot pre-empt the click script's synchronous prefix; a nonce mark
queued behind an already-running script cannot stop ``btn.click()`` when that
click runs before the script's first await. The permit stamped on ``performance.now()``
before the click script is dispatched is what the prefix reads before clicking.
A send poll that outlives the CDP wait must not click: the page gives up when
``performance.now()`` passes that permit, and a per-call abort nonce blocks a
late evaluate without affecting the next send. Python records a monotonic
click fact immediately before the click script is sent; a permit-stamp timeout
leaves that fact false. A page dict with ``send_clicked`` false, no
``clicked_unknown``, and reason ``no_composer``, ``composer_text_mismatch``,
or ``no_send_button`` is a separate decisive-refusal fact: the script finished
and did not click. That fact does not clear the monotonic flag. Enter may run
while it is set; dispatching Enter clears it. A page ``cdp_eval_timeout`` is
not a refusal.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import logging
import re
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# Per-call nonce for the in-flight send poll. A context var keeps ``_cdp_eval``'s
# signature stable for existing test shims that do not accept new kwargs.
_send_abort_nonce: contextvars.ContextVar[str] = contextvars.ContextVar(
    "composer_send_abort_nonce",
    default="",
)

# Monotonic for one paste attempt. Set immediately before the click script is
# sent. Reset by the paste exit gate. A later failure result cannot clear it.
# A decisive page refusal is a second fact, not a reason to clear this one.
_click_dispatched: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "composer_click_dispatched",
    default=False,
)

# Set when the click script returns a decisive no-click. Cleared when Enter
# is dispatched, and reset with the click fact at the start of an attempt.
_click_decisively_refused: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "composer_click_decisively_refused",
    default=False,
)

_DECISIVE_NO_CLICK_REASONS = frozenset(
    {"no_composer", "composer_text_mismatch", "no_send_button"}
)


def _mark_click_dispatched() -> None:
    """Record that the click script is about to be sent on this task.

    The flag stays true until ``_reset_click_dispatched`` starts another
    attempt. A later failure result cannot clear it. A decisive page
    refusal is recorded separately and does not clear this flag.
    """
    _click_dispatched.set(True)


def _click_was_dispatched() -> bool:
    """True after ``_mark_click_dispatched`` in the current context."""
    return bool(_click_dispatched.get())


def _mark_click_decisively_refused() -> None:
    """Record that the click script finished and did not click.

    Does not clear the monotonic click fact. ``_mark_enter_dispatched``
    clears this fact when a later Enter key is sent.
    """
    _click_decisively_refused.set(True)


def _click_was_decisively_refused() -> bool:
    """True after a decisive no-click page result until Enter is dispatched."""
    return bool(_click_decisively_refused.get())


def _page_decisively_refused_click(value: dict[str, Any]) -> bool:
    """True when the finished click script did not call ``btn.click()``.

    Requires ``send_clicked`` false, no ``clicked_unknown``, and a reason in
    ``no_composer``, ``composer_text_mismatch``, or ``no_send_button``.
    ``cdp_eval_timeout`` is not decisive: the page can return it after the
    poll, and a Python timeout is ``clicked_unknown``. Either may have clicked.
    """
    if value.get("clicked_unknown"):
        return False
    if value.get("send_clicked") is not False:
        return False
    return value.get("reason") in _DECISIVE_NO_CLICK_REASONS


def _mark_enter_dispatched() -> None:
    """Record that Enter is being sent and a prior no-click refusal is over.

    Sets the same monotonic click fact as a send click, then clears a
    decisive click refusal so an unverified Enter is not retried. Callers
    must skip this on a preflight refusal, before any key is sent.
    """
    _mark_click_dispatched()
    _click_decisively_refused.set(False)


def _reset_click_dispatched() -> tuple[
    contextvars.Token[bool], contextvars.Token[bool]
]:
    """Clear the click fact and the decisive-refusal fact for one attempt.

    Returns both reset tokens. ``_restore_click_dispatched`` puts them back
    so a nested attempt does not leak either fact into its caller.
    """
    return (
        _click_dispatched.set(False),
        _click_decisively_refused.set(False),
    )


def _restore_click_dispatched(
    token: tuple[contextvars.Token[bool], contextvars.Token[bool]],
) -> None:
    """Put both click facts back to the values from before this paste attempt."""
    click_token, refused_token = token
    _click_dispatched.reset(click_token)
    _click_decisively_refused.reset(refused_token)


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
    ourDraftWriter: '',
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

SEND_BTN_WAIT_CEILING_MS = 3000
SEND_BTN_POLL_INTERVAL_MS = 150
SEND_BTN_CENSUS_BUTTONS_CAP = 20
SEND_BTN_CENSUS_ATTR_CAP = 200
# Runtime.evaluate must outlive async send-button polling in the page.
SEND_BTN_CDP_EVAL_TIMEOUT_S = max(8.0, SEND_BTN_WAIT_CEILING_MS / 1000.0 + 2.0)
# Page poll returns before that wait so a late tick cannot click after Python gave up.
SEND_BTN_PAGE_TIMEOUT_MARGIN_MS = 1000


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


def _set_composer_body(
    selector: str,
    text: str,
    *,
    preserve_newlines: bool = False,
    draft_writer: str = "",
) -> str:
    find = _find_composer_js(selector)
    line = _dom_paste_text(text, preserve_newlines=preserve_newlines)
    msg = json.dumps(line)
    writer = json.dumps(str(draft_writer or ""))
    return (
        f"(()=>{{const el=({find});"
        "if(!el)return{ok:false,reason:'no_composer'};"
        f"const line={msg};"
        f"const draftWriter={writer};"
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
        "g.ourDraftWriter=draftWriter;"
        "return{ok:true,sig:after};})()"
    )


def _click_send_own_draft_body(
    selector: str, *, draft_writer: str = "", abort_nonce: str = ""
) -> str:
    """Send only when composer draft matches this writer's ourDraftSig."""
    find = _find_composer_js(selector)
    btn_sels = json.dumps(list(_SEND_BTN_SELECTORS))
    expect_writer = json.dumps(str(draft_writer or ""))
    nonce_js = json.dumps(str(abort_nonce))
    return (
        f"(()=>{{const el=({find});"
        "if(!el)return{ok:false,reason:'no_composer',send_clicked:false};"
        "const g=window.__ulgOperatorActivity=window.__ulgOperatorActivity||{};"
        f"const abortNonce={nonce_js};"
        "const cleanup=()=>{if(!abortNonce)return;"
        "const pm=g.composerSendPermits;if(pm)delete pm[abortNonce];"
        "const am=g.composerSendAbortNonces;if(am)delete am[abortNonce];};"
        "const sendGivenUp=()=>{"
        "const p=g.composerSendPermits&&g.composerSendPermits[abortNonce];"
        "if(typeof p!=='number')return true;"
        "if(performance.now()>=p)return true;"
        "return !!(abortNonce&&g.composerSendAbortNonces&&g.composerSendAbortNonces[abortNonce]);"
        "};"
        f"const expectWriter={expect_writer};"
        "const writer=String(g.ourDraftWriter||'').trim();"
        "const sig=String(g.ourDraftSig||'').trim();"
        "const draft=(el.innerText||el.value||'').trim();"
        "if(expectWriter&&( !writer||writer!==expectWriter))"
        "{cleanup();return{ok:false,reason:'foreign_draft',send_clicked:false,draft,sig,writer};}"
        "if(!sig||draft!==sig)"
        "{cleanup();return{ok:false,reason:'draft_not_ours',send_clicked:false,draft,sig};}"
        "if(sendGivenUp())"
        "{cleanup();return{ok:false,reason:'cdp_eval_timeout',send_clicked:false};}"
        f"const root=el.closest('form')||el.parentElement||document;"
        f"const btnSels={btn_sels};"
        "let btn=null;"
        "for(const sel of btnSels){btn=root.querySelector(sel);if(btn)break;}"
        "if(!btn){cleanup();return{ok:false,reason:'no_send_button',send_clicked:false};}"
        "btn.click();"
        "g.ourDraftSig='';"
        "g.ourDraftWriter='';"
        "cleanup();return{ok:true,send_clicked:true};})()"
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
        "g.ourDraftWriter='';"
        "const after=(el.innerText||el.value||'').trim();"
        "return{ok:true,cleared:after.length===0,draft:after};})()"
    )


async def clear_composer_draft_dom_no_focus(
    ws_url: str, *, selector: str
) -> dict[str, Any]:
    raw = await _cdp_eval(ws_url, clear_composer_draft_dom_no_focus_js(selector))
    return raw if isinstance(raw, dict) else {"ok": False}


def _click_send_body(
    selector: str,
    line: str,
    *,
    preserve_newlines: bool = False,
    abort_nonce: str = "",
    eval_timeout_s: float | None = None,
) -> str:
    """Build async page JS that polls for an enabled send control after insert.

    Returns a ``(async () => …)()`` expression (Promise) for CDP ``Runtime.evaluate``
    with ``awaitPromise=True``. Python must evaluate ``_composer_send_permit_expression``
    for this ``abort_nonce`` before dispatching this script. The permit deadline lives
    on the page clock only; this script does not restamp it. Cancel cannot pre-empt
    the synchronous prefix: if the send button is found on the first scan, ``btn.click()``
    runs before the first ``await``, so a nonce mark still queued on CDP cannot prevent
    that click — the stamped permit is what ``sendGivenUp()`` reads first.

    Immediate ``no_composer`` and ``composer_text_mismatch`` results do not wait. After
    the first scan miss, each retry awaits ``setTimeout`` for ``SEND_BTN_POLL_INTERVAL_MS``
    up to ``SEND_BTN_WAIT_CEILING_MS``. Final ``no_send_button`` includes a ``census``
    object (``buttons`` capped at 20, each ``aria_label``, ``data_testid``, and ``type``
    sliced to ``SEND_BTN_CENSUS_ATTR_CAP``, ``buttons_total`` uncapped). The poll
    returns ``cdp_eval_timeout`` without clicking when the permit is missing or expired,
    or when ``abort_nonce`` is set on ``composerSendAbortNonces`` (that nonce only).
    Every return path deletes this call's permit and abort-nonce map entries only.
    """
    find = _find_composer_js(selector)
    lj = json.dumps(_dom_paste_text(line, preserve_newlines=preserve_newlines))
    btn_sels = json.dumps(list(_SEND_BTN_SELECTORS))
    wait_ceiling = int(SEND_BTN_WAIT_CEILING_MS)
    poll_interval = int(SEND_BTN_POLL_INTERVAL_MS)
    btn_cap = int(SEND_BTN_CENSUS_BUTTONS_CAP)
    attr_cap = int(SEND_BTN_CENSUS_ATTR_CAP)
    timeout_s = (
        float(SEND_BTN_CDP_EVAL_TIMEOUT_S)
        if eval_timeout_s is None
        else float(eval_timeout_s)
    )
    page_budget_ms = max(
        0, int(timeout_s * 1000) - int(SEND_BTN_PAGE_TIMEOUT_MARGIN_MS)
    )
    nonce_js = json.dumps(str(abort_nonce))
    return (
        f"(async ()=>{{const g=window.__ulgOperatorActivity=window.__ulgOperatorActivity||{{}};"
        f"const abortNonce={nonce_js};"
        "const cleanup=()=>{if(!abortNonce)return;"
        "const pm=g.composerSendPermits;if(pm)delete pm[abortNonce];"
        "const am=g.composerSendAbortNonces;if(am)delete am[abortNonce];};"
        "const sendGivenUp=()=>{"
        "const p=g.composerSendPermits&&g.composerSendPermits[abortNonce];"
        "if(typeof p!=='number')return true;"
        "if(performance.now()>=p)return true;"
        "return !!(abortNonce&&g.composerSendAbortNonces&&g.composerSendAbortNonces[abortNonce]);"
        "};"
        f"const el=({find});"
        "if(!el){cleanup();return{ok:false,reason:'no_composer',send_clicked:false};}"
        f"const text=(el.innerText||el.value||'').trim(),line={lj};"
        "if(!text||(text!==line.trim()&&!/^BRIDGE_WAKE\\s+thread=\\S+\\s+turn=\\d+(?:,\\d+)*$/.test(text)))"
        "{cleanup();return{ok:false,reason:'composer_text_mismatch',send_clicked:false};}"
        f"const root=el.closest('form')||el.parentElement||document;"
        f"const btnSels={btn_sels};"
        f"const waitCeilingMs={wait_ceiling};"
        f"const pollIntervalMs={poll_interval};"
        "const findSendBtn=()=>{"
        "let b=null;"
        "for(const sel of btnSels){b=root.querySelector(sel);if(b)break;}"
        "return b;"
        "};"
        "const buildCensus=(elapsedMs)=>{"
        "const buttons=[];"
        "let buttonsTotal=0;"
        "root.querySelectorAll('button,[role=button]').forEach((node)=>{"
        "buttonsTotal+=1;"
        f"if(buttons.length>={btn_cap})return;"
        "const rect=node.getBoundingClientRect();"
        "const capAttr=(name)=>{const v=node.getAttribute(name);"
        f"return v==null?null:String(v).slice(0,{attr_cap});}};"
        "buttons.push({"
        "aria_label:capAttr('aria-label'),"
        "data_testid:capAttr('data-testid'),"
        "type:capAttr('type'),"
        "disabled:!!(node.disabled||node.hasAttribute('disabled')),"
        "non_zero_rect:rect.width>0&&rect.height>0,"
        "});"
        "});"
        "let outer=String(root.outerHTML||'');"
        "if(outer.length>4000)outer=outer.slice(0,4000);"
        "return{"
        "root_tag:String(root.tagName||'').toLowerCase(),"
        "root_outer_html:outer,"
        "buttons,"
        "buttons_total:buttonsTotal,"
        "document_visibility_state:document.visibilityState||'',"
        "document_has_focus:document.hasFocus(),"
        "elapsed_ms:elapsedMs,"
        "};"
        "};"
        "const t0=Date.now();"
        "const deadline=t0+waitCeilingMs;"
        "let btn=findSendBtn();"
        "while(!btn&&Date.now()<deadline&&!sendGivenUp()){"
        "await new Promise((r)=>setTimeout(r,pollIntervalMs));"
        "btn=findSendBtn();"
        "}"
        "if(sendGivenUp()){cleanup();return{ok:false,reason:'cdp_eval_timeout',send_clicked:false};}"
        "if(!btn){"
        "cleanup();return{ok:false,reason:'no_send_button',send_clicked:false,"
        "census:buildCensus(Date.now()-t0)};"
        "}"
        "if(sendGivenUp()){cleanup();return{ok:false,reason:'cdp_eval_timeout',send_clicked:false};}"
        "btn.click();"
        "g.ourDraftSig='';"
        "g.ourDraftWriter='';"
        "cleanup();return{ok:true,send_clicked:true};})()"
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


def _composer_send_permit_expression(nonce: str, page_budget_ms: int) -> str:
    """Page JS that stamps one send permit deadline on ``performance.now()``."""
    nonce_js = json.dumps(str(nonce))
    budget = int(page_budget_ms)
    return (
        "(()=>{const g=window.__ulgOperatorActivity=window.__ulgOperatorActivity||{};"
        "const m=g.composerSendPermits=g.composerSendPermits||{};"
        f"m[{nonce_js}]=performance.now()+{budget};"
        "return{ok:true};})()"
    )


def _composer_send_abort_expression(nonce: str) -> str:
    """Page JS that records one send-poll nonce as aborted.

    Other nonces already on ``composerSendAbortNonces`` stay, so a later send is
    not refused and an older poll still matches its own nonce.
    """
    nonce_js = json.dumps(str(nonce))
    return (
        "(()=>{const g=window.__ulgOperatorActivity=window.__ulgOperatorActivity||{};"
        "const m=g.composerSendAbortNonces=g.composerSendAbortNonces||{};"
        f"m[{nonce_js}]=true;return{{ok:true}};}})()"
    )


async def _mark_composer_send_abort(ws: Any, nonce: str) -> None:
    if not nonce:
        return
    try:
        await ws.send(
            json.dumps(
                {
                    "id": 2,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": _composer_send_abort_expression(nonce),
                        "awaitPromise": False,
                        "returnByValue": True,
                    },
                }
            )
        )
        await asyncio.wait_for(_cdp_response(ws, 2), 1.0)
    except Exception:
        return


async def _cdp_eval(
    ws_url: str,
    expression: str,
    *,
    await_promise: bool = False,
    timeout_s: float = 8.0,
) -> Any:
    """Evaluate ``expression`` in the page.

    On timeout or cancellation, if ``_send_abort_nonce`` is set, mark that nonce
    on the page before the socket closes. Does not call
    ``Runtime.terminateExecution`` (that stops the target's next script, which
    may be the app). Other exceptions propagate with no mark.
    """
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
        try:
            raw = await asyncio.wait_for(_cdp_response(ws, 1), timeout_s)
        except (TimeoutError, asyncio.CancelledError) as exc:
            nonce = _send_abort_nonce.get()
            if nonce:
                await _mark_composer_send_abort(ws, nonce)
            if isinstance(exc, asyncio.CancelledError):
                task = asyncio.current_task()
                if task is not None:
                    task.cancel()
                raise
            raise
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
        foreign = probe_foreign_draft(probe, expect_draft_sha256=expect_draft_sha256)
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
    ws_url: str,
    *,
    selector: str,
    text: str,
    preserve_newlines: bool = False,
    draft_writer: str = "",
) -> dict[str, Any]:
    value = await _cdp_eval(
        ws_url,
        _set_composer_body(
            selector,
            text,
            preserve_newlines=preserve_newlines,
            draft_writer=draft_writer,
        ),
    )
    if not isinstance(value, dict):
        return {"ok": False, "reason": "paste_failed"}
    if not value.get("ok"):
        return value
    return {"ok": True}


async def dom_click_send(
    ws_url: str, *, selector: str, line: str, preserve_newlines: bool = False
) -> dict[str, Any]:
    """Click send when composer text matches ``line``, or return without clicking.

    Stamps a page-clock send permit via CDP before dispatching the click script.
    Cancel cannot pre-empt that script's synchronous prefix; a nonce mark queued
    behind an already-running script cannot stop ``btn.click()`` when the click
    runs before the script's first await — the permit stamped earlier is what the
    prefix reads before clicking. The page poll returns ``reason=cdp_eval_timeout``
    when ``performance.now()`` passes that permit or the abort nonce is set.
    Python returns the same dict when the permit or click CDP wait hits
    ``TimeoutError`` (click script is not sent if the permit stamp times out).
    ``CancelledError`` propagates after the abort nonce is marked. A later call
    uses a new nonce. ``no_send_button`` may include ``census`` (logged at WARNING).
    The click fact is set immediately before the click evaluate and stays false
    when the permit stamp times out, so a later ``cdp_eval_timeout`` result is
    not treated as proof that the click did not run. A returned dict with
    ``send_clicked`` false, no ``clicked_unknown``, and reason ``no_composer``,
    ``composer_text_mismatch``, or ``no_send_button`` records a decisive
    refusal: the script finished and did not click. That refusal does not
    clear the click fact. ``cdp_eval_timeout`` does not record it.
    """
    nonce = uuid.uuid4().hex
    token = _send_abort_nonce.set(nonce)
    timeout_s = float(SEND_BTN_CDP_EVAL_TIMEOUT_S)
    page_budget_ms = max(
        1, int(timeout_s * 1000) - int(SEND_BTN_PAGE_TIMEOUT_MARGIN_MS)
    )
    try:
        try:
            await _cdp_eval(
                ws_url,
                _composer_send_permit_expression(nonce, page_budget_ms),
                await_promise=False,
                timeout_s=timeout_s,
            )
        except TimeoutError:
            return {"ok": False, "reason": "cdp_eval_timeout", "send_clicked": False}
        _mark_click_dispatched()
        value = await _cdp_eval(
            ws_url,
            _click_send_body(
                selector,
                line,
                preserve_newlines=preserve_newlines,
                abort_nonce=nonce,
                eval_timeout_s=timeout_s,
            ),
            await_promise=True,
            timeout_s=timeout_s,
        )
    except TimeoutError:
        return {
            "ok": False,
            "reason": "cdp_eval_timeout",
            "send_clicked": False,
            "clicked_unknown": True,
            "retryable": False,
        }
    finally:
        _send_abort_nonce.reset(token)
    if not isinstance(value, dict):
        return {"ok": False, "send_clicked": False}
    if _page_decisively_refused_click(value):
        _mark_click_decisively_refused()
    if value.get("reason") == "no_send_button" and value.get("census") is not None:
        logger.warning(
            "dom_click_send no_send_button census=%s",
            json.dumps(value["census"], ensure_ascii=False, default=str),
        )
    return value


async def dom_click_send_own_draft(
    ws_url: str, *, selector: str, draft_writer: str = ""
) -> dict[str, Any]:
    """Click send when composer text equals ourDraftSig (submit-without-text path).

    Stamps the same page-clock send permit and abort nonce as ``dom_click_send``
    before evaluating the click script so cancel and permit expiry block the click.
    The click fact is set immediately before that click evaluate, and a permit
    timeout returns without setting it.
    """
    nonce = uuid.uuid4().hex
    token = _send_abort_nonce.set(nonce)
    timeout_s = float(SEND_BTN_CDP_EVAL_TIMEOUT_S)
    page_budget_ms = max(
        1, int(timeout_s * 1000) - int(SEND_BTN_PAGE_TIMEOUT_MARGIN_MS)
    )
    try:
        try:
            await _cdp_eval(
                ws_url,
                _composer_send_permit_expression(nonce, page_budget_ms),
                await_promise=False,
                timeout_s=timeout_s,
            )
        except TimeoutError:
            return {"ok": False, "reason": "cdp_eval_timeout", "send_clicked": False}
        _mark_click_dispatched()
        value = await _cdp_eval(
            ws_url,
            _click_send_own_draft_body(
                selector, draft_writer=draft_writer, abort_nonce=nonce
            ),
            timeout_s=timeout_s,
        )
    except TimeoutError:
        return {
            "ok": False,
            "reason": "cdp_eval_timeout",
            "send_clicked": False,
            "clicked_unknown": True,
            "retryable": False,
        }
    finally:
        _send_abort_nonce.reset(token)
    if not isinstance(value, dict):
        return {"ok": False, "send_clicked": False}
    return value


async def dom_insert_and_send(
    ws_url: str,
    *,
    selector: str,
    line: str,
    preserve_newlines: bool = False,
    draft_writer: str = "",
) -> dict[str, Any]:
    """Insert once via DOM; send via async send-button poll (no Enter, no focus()).

    Calls ``dom_set_composer_text`` exactly once, then ``dom_click_send``. On send
    failure the return includes ``reason`` and ``send_fallback_clicked: False``; when
    ``dom_click_send`` returned ``census``, that key is forwarded unchanged.
    """
    ins = await dom_set_composer_text(
        ws_url,
        selector=selector,
        text=line,
        preserve_newlines=preserve_newlines,
        draft_writer=draft_writer,
    )
    if not ins.get("ok"):
        return ins
    send = await dom_click_send(
        ws_url, selector=selector, line=line, preserve_newlines=preserve_newlines
    )
    if not send.get("ok"):
        out: dict[str, Any] = {
            "ok": False,
            "reason": str(send.get("reason") or "send_failed"),
            "send_fallback_clicked": False,
        }
        if send.get("census") is not None:
            out["census"] = send["census"]
        return out
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
