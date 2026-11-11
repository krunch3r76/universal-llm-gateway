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
import unicodedata
import uuid
from typing import Any

from cursor_bridge.chat_target import stale_wake_lines_js

logger = logging.getLogger(__name__)

_ULG_NORM_COMPOSER_JS = r"""
function ulgNormalizeComposerText(raw) {
  let s = String(raw ?? '').normalize('NFC');
  s = s.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  s = s.replace(/\u00a0/g, ' ').replace(/\u202f/g, ' ');
  return s;
}
function ulgPlainHtml(raw) {
  const esc = String(raw ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  return '<p style="white-space:pre-wrap">' + esc.split('\n').join('<br>') + '</p>';
}
function ulgComposerDomEmpty(el) {
  if (!el) return true;
  const raw = el.innerText || el.value || '';
  const norm = ulgNormalizeComposerText(raw);
  const html = String(el.innerHTML || '');

  // Two-char backslash + n is foreign content, not an empty newline placeholder.
  if (raw === '\\n') return false;

  // Protect human whitespace and visible text; do not trim it away.
  if (/[ \t]/.test(raw) || /[ \t]/.test(norm)) return false;
  if (norm !== '' && norm !== '\n') {
    const withoutNl = norm.replace(/\n/g, '');
    if (withoutNl.length > 0) return false;
  }

  const ed = el.editor;
  if (ed && ed.state && ed.state.doc) {
    const doc = ed.state.doc;
    const docText = typeof doc.textContent === 'string' ? doc.textContent : '';
    if (docText === '\\n') return false;
    if (/[ \t]/.test(docText)) return false;
    if (docText !== '' && docText !== '\n') {
      const nd = ulgNormalizeComposerText(docText);
      if (nd !== '' && nd !== '\n') return false;
    }
    if (typeof doc.descendants === 'function') {
      let blocked = false;
      doc.descendants((node) => {
        if (blocked) return false;
        if (!node) return;
        if (node.isText && node.text) {
          const t = String(node.text);
          if (t === '\\n') { blocked = true; return false; }
          const n = ulgNormalizeComposerText(t);
          if (/[ \t]/.test(n)) { blocked = true; return false; }
          if (n.replace(/\n/g, '').length > 0) { blocked = true; return false; }
          return;
        }
        if (node.isAtom) { blocked = true; return false; }
        const nm = node.type && node.type.name;
        if (nm && (nm === 'image' || nm === 'attachment' || nm === 'file')) {
          blocked = true;
          return false;
        }
      });
      if (blocked) return false;
    }
  }

  const hasTrailingBreak = html.indexOf('ProseMirror-trailingBreak') >= 0;
  const placeholderHtml = hasTrailingBreak && (
    html.indexOf('is-editor-empty') >= 0 || html.indexOf('is-empty') >= 0
  );

  if (placeholderHtml && (norm === '\n' || norm === '')) return true;
  if (hasTrailingBreak && norm === '\n' && ed && ed.isEmpty === true) return true;
  if (norm === '' && html.trim() === '') return true;
  if (ed && ed.isEmpty === true && norm === '' && hasTrailingBreak) return true;

  // ChatGPT can leave one paragraph, one trailing BR, and a newline after a
  // sent draft, with no editor object and no is-editor-empty class.
  if (!ed && (norm === '' || norm === '\n')) {
    const elementChildren = [...(el.children || [])].filter((n) => n && n.tagName);
    const block = elementChildren.length === 1 ? elementChildren[0] : null;
    const blockKids = block && block.children ? [...block.children] : [];
    const br = blockKids.length === 1 ? blockKids[0] : null;
    const brClass = br
      ? String((br.getAttribute && br.getAttribute('class')) || br.className || '')
      : '';
    const onlyTrailingBreak = !!block
      && /^(P|DIV)$/.test(String(block.tagName || ''))
      && !!br
      && String(br.tagName || '') === 'BR'
      && brClass.indexOf('ProseMirror-trailingBreak') >= 0;
    const structuralReject = (node) => {
      if (!node) return false;
      const ce = node.getAttribute && node.getAttribute('contenteditable');
      if (String(ce || '').toLowerCase() === 'false') return true;
      const cls = String((node.getAttribute && node.getAttribute('class')) || node.className || '');
      if (cls.indexOf('attachment') >= 0) return true;
      return [...(node.children || [])].some(structuralReject);
    };
    const foreign = typeof el.querySelector === 'function' && el.querySelector(
      'img, atom, svg, a[href], [data-testid*="attachment" i], [data-testid*="file-chip" i], [class*="chip" i]'
    );
    if (onlyTrailingBreak && !foreign && !structuralReject(el)) return true;
  }

  return false;
}
function ulgIsStopControl(btn) {
  if (!btn) return true;
  const label = String((btn.getAttribute && btn.getAttribute('aria-label')) || '');
  const testid = String((btn.getAttribute && btn.getAttribute('data-testid')) || '');
  return /stop/i.test(label) || /stop/i.test(testid);
}
function ulgHasForeignForm(root, el) {
  if (!root || typeof root.querySelectorAll !== 'function') return false;
  const own = el.closest && el.closest('form');
  const forms = root.querySelectorAll('form');
  for (let i = 0; i < forms.length; i += 1) {
    const f = forms[i];
    if (own && f === own) continue;
    return true;
  }
  return false;
}
function ulgHasForeignTextControl(root, el) {
  if (!root || typeof root.querySelectorAll !== 'function') return false;
  let bad = false;
  root.querySelectorAll('textarea,input').forEach((n) => {
    if (bad || n === el) return;
    const tag = String(n.tagName || '').toUpperCase();
    const typ = String((n.getAttribute && n.getAttribute('type')) || n.type || '')
      .toLowerCase();
    if (tag === 'TEXTAREA' || typ === 'text' || typ === 'search' || typ === 'email' || typ === '') {
      bad = true;
    }
  });
  return bad;
}
function ulgScopeSelector(sel) {
  return String(sel || '').replace(/:not\(\[disabled\]\)/g, '');
}
function ulgHasStopControl(root) {
  if (!root || typeof root.querySelectorAll !== 'function') return false;
  const nodes = root.querySelectorAll('button,[role="button"]');
  for (let i = 0; i < nodes.length; i += 1) {
    if (ulgIsStopControl(nodes[i])) return true;
  }
  return false;
}
function ulgHasRecognizableSendControl(root, el, btnSels) {
  if (!root) return false;
  const ownForm = el.closest && el.closest('form');
  const allowGenericSubmit = !!(ownForm && root === ownForm);
  for (const sel of btnSels) {
    if (!allowGenericSubmit && sel.indexOf('type="submit"') >= 0) continue;
    const btn = root.querySelector(ulgScopeSelector(sel));
    if (!btn) continue;
    if (ulgIsStopControl(btn)) continue;
    return true;
  }
  return false;
}
function ulgFindSendBtn(root, el, btnSels) {
  if (!root) return null;
  const ownForm = el.closest && el.closest('form');
  const allowGenericSubmit = !!(ownForm && root === ownForm);
  for (const sel of btnSels) {
    if (!allowGenericSubmit && sel.indexOf('type="submit"') >= 0) continue;
    const btn = root.querySelector(sel);
    if (!btn) continue;
    if (btn.disabled || btn.hasAttribute('disabled')) continue;
    if (ulgIsStopControl(btn)) continue;
    return btn;
  }
  return null;
}
function ulgComposerSendRoot(el) {
  if (!el) return null;
  const form = el.closest('form');
  if (form) return form;
  const isEditable = (n) => {
    if (!n || n.nodeType !== 1) return false;
    if (n.isContentEditable) return true;
    const ce = n.getAttribute && n.getAttribute('contenteditable');
    return String(ce || '').toLowerCase() === 'true';
  };
  const isVisibleEditable = (n) => {
    if (!isEditable(n)) return false;
    if (!n.isConnected) return false;
    const rect = n.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const hasSecondComposer = (root) => {
    if (!root || typeof root.querySelectorAll !== 'function') return false;
    let count = 0;
    root.querySelectorAll('[contenteditable="true"]').forEach((node) => {
      if (isVisibleEditable(node)) count += 1;
    });
    return count > 1;
  };
  const ULG_SEND_BTN_SELECTORS = [
    'button[aria-label="Send message"]:not([disabled])',
    'button[aria-label="Send prompt"]:not([disabled])',
    'button[aria-label="Send"]:not([disabled])',
    'button[data-testid="send-button"]:not([disabled])',
    'button[type="submit"]:not([disabled])',
  ];
  let cur = el.parentElement;
  for (let step = 0; cur && step < 8; step += 1, cur = cur.parentElement) {
    if (cur === document.body || cur === document.documentElement) break;
    if (ulgHasForeignForm(cur, el)) continue;
    if (ulgHasForeignTextControl(cur, el)) continue;
    if (hasSecondComposer(cur)) continue;
    if (ulgHasStopControl(cur)) return cur;
    if (!ulgHasRecognizableSendControl(cur, el, ULG_SEND_BTN_SELECTORS)) continue;
    return cur;
  }
  return null;
}
"""


def normalize_composer_text(text: str) -> str:
    """Normalize for equality: NFC, newlines, NBSP. Ends and indentation stay."""
    s = unicodedata.normalize("NFC", text or "")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\u00a0", " ").replace("\u202f", " ")
    return s


def composer_text_semantic_equals(a: str, b: str) -> bool:
    """True when two composer payloads match after semantic normalization."""
    return normalize_composer_text(a) == normalize_composer_text(b)


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
def _is_stale_wake_draft(draft: str) -> bool:
    from cursor_bridge.chat_target import is_stale_wake_draft

    return is_stale_wake_draft(draft)


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

_WORK_EDITOR_RESOLVE_JS = r"""
function ulgWorkEditorLabeled(el) {
  const label = String((el && el.getAttribute && el.getAttribute('aria-label')) || '').toLowerCase();
  return label.includes('work with chatgpt');
}
function ulgCssHides(node) {
  if (!node || typeof window.getComputedStyle !== 'function') return false;
  const st = window.getComputedStyle(node);
  if (!st) return false;
  if (st.visibility === 'hidden' || st.display === 'none') return true;
  const raw = st.opacity;
  return raw != null && raw !== '' && Number(raw) === 0;
}
function ulgWorkEditorRenderedVisible(el) {
  if (!el) return false;
  if (el.isConnected === false) return false;
  if ('inert' in el && el.inert) return false;
  let p = el.parentElement;
  while (p) {
    if ('inert' in p && p.inert) return false;
    if (ulgCssHides(p)) return false;
    p = p.parentElement;
  }
  const rect = el.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return false;
  if (ulgCssHides(el)) return false;
  return true;
}
function ulgAnyHiddenWorkEditor() {
  if (!document || typeof document.querySelectorAll !== 'function') return false;
  const nodes = document.querySelectorAll('[contenteditable="true"][role="textbox"]');
  for (const el of nodes) {
    if (!ulgWorkEditorLabeled(el)) continue;
    if (!ulgWorkEditorRenderedVisible(el)) return true;
  }
  return false;
}
function ulgIsHiddenWorkHit(el) {
  if (!el || !ulgWorkEditorLabeled(el)) return false;
  const tag = String(el.tagName || '').toUpperCase();
  if (tag === 'TEXTAREA' || tag === 'INPUT') return false;
  return !ulgWorkEditorRenderedVisible(el);
}
function ulgComposerSurfaceText(el) {
  if (!el) return '';
  const tag = String(el.tagName || '').toUpperCase();
  if (tag === 'TEXTAREA' || tag === 'INPUT') return String(el.value || '');
  return String(el.innerText || '');
}
function ulgResolveVisibleWorkComposer() {
  if (!document || typeof document.querySelectorAll !== 'function') {
    return {ok: false, reason: 'no_composer'};
  }
  const candidates = [];
  const nodes = document.querySelectorAll('[contenteditable="true"][role="textbox"]');
  for (const el of nodes) {
    if (!el || !ulgWorkEditorLabeled(el)) continue;
    if (!ulgWorkEditorRenderedVisible(el)) continue;
    if (el.querySelector && el.querySelector('textarea,input[type="text"]')) continue;
    candidates.push(el);
  }
  if (!candidates.length) return {ok: false, reason: 'no_composer'};
  if (candidates.length > 1) return {ok: false, reason: 'ambiguous_work_composer'};
  return {ok: true, el: candidates[0]};
}
"""

READ_OPERATOR_BUSY_JS = (
    "(() => {\n"
    + _WORK_EDITOR_RESOLVE_JS
    + r"""
  const g = window.__ulgOperatorActivity || {};
  const now = Date.now();
  const last = Number(g.lastInputMs || 0);
  const idleMs = 10000;
  const operatorActive = last > 0 && (now - last) < idleMs;
  const sels = %SELECTORS%;
  let el = null;
  const work = ulgResolveVisibleWorkComposer();
  if (work.ok && work.el && !ulgIsHiddenWorkHit(work.el)) {
    el = work.el;
  } else if (work.reason !== 'ambiguous_work_composer') {
    for (const s of sels) {
      const listed = typeof document.querySelectorAll === 'function'
        ? document.querySelectorAll(s) : [];
      for (const hit of listed) {
        if (!ulgIsHiddenWorkHit(hit)) { el = hit; break; }
      }
      if (!el) {
        const single = document.querySelector(s);
        if (single && !ulgIsHiddenWorkHit(single)) el = single;
      }
      if (el) break;
    }
  }
  const draft = ulgComposerSurfaceText(el).trim();
  const sig = String(g.ourDraftSig || '').trim();
  const staleWake = %STALE_WAKE_JS%;
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
)

_SEND_BTN_SELECTORS = (
    'button[aria-label="Send message"]:not([disabled])',
    'button[aria-label="Send prompt"]:not([disabled])',
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
        return text or ""
    return _flatten_paste_line(text)


def _selector_list(selector: str) -> list[str]:
    return [s.strip() for s in selector.split(",") if s.strip()]


def _find_composer_js(selector: str) -> str:
    """Resolve one composer, skipping hidden Work hits.

    A unique visible Work editor wins. Ambiguous visible Work editors return
    null. Otherwise the selector list may return a visible textarea, input,
    or ``el.editor`` host. A hidden Work textbox is never that host, and its
    presence does not veto the legacy control.
    """
    sels = json.dumps(_selector_list(selector) or [selector])
    return (
        f"(()=>{{{_WORK_EDITOR_RESOLVE_JS}"
        f"const sels={sels};"
        "const work=ulgResolveVisibleWorkComposer();"
        "if(work.ok&&work.el&&!ulgIsHiddenWorkHit(work.el))return work.el;"
        "if(work.reason==='ambiguous_work_composer')return null;"
        "for(const s of sels){"
        "const listed=typeof document.querySelectorAll==='function'"
        "?document.querySelectorAll(s):[];"
        "for(const hit of listed){if(hit&&!ulgIsHiddenWorkHit(hit))return hit;}"
        "const single=document.querySelector(s);"
        "if(single&&!ulgIsHiddenWorkHit(single))return single;"
        "}"
        "return null;})()"
    )


def work_native_unsupported_probe_js(*, expected_url: str) -> str:
    """Return whether hydrated Work needs guarded native insert (before any mutation)."""
    exp = json.dumps(str(expected_url or "").split("?")[0])
    return (
        f"(()=>{{{_ULG_NORM_COMPOSER_JS}{_WORK_EDITOR_RESOLVE_JS}"
        f"const expectUrl={exp};"
        "const here=String(location.href||'').split('?')[0];"
        "if(expectUrl&&here!==expectUrl)return{ok:false,eligible:false,reason:'url_mismatch'};"
        "const resolved=ulgResolveVisibleWorkComposer();"
        "if(!resolved.ok)return{ok:false,eligible:false,reason:resolved.reason||'no_composer'};"
        "const el=resolved.el;"
        "if(el.editor&&el.editor.commands&&typeof el.editor.commands.setContent==='function'){"
        "return{ok:true,eligible:false,reason:'work_dom_api_available'};}"
        "if(!el.pmViewDesc)return{ok:false,eligible:false,reason:'work_native_unsupported'};"
        "return{ok:true,eligible:true};})()"
    )


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
    from cursor_bridge.chat_target import stale_wake_lines_js

    stale_js = stale_wake_lines_js("normDraft")
    return (
        f"(()=>{{{_ULG_NORM_COMPOSER_JS}"
        f"const el=({find});"
        "if(!el)return{ok:false,reason:'no_composer',phase:'insertion_failed',inserted:false,pasted:false};"
        f"const line={msg};"
        f"const expectWriter={writer};"
        "const g=window.__ulgOperatorActivity=window.__ulgOperatorActivity||{};"
        "const normLine=ulgNormalizeComposerText(line);"
        "const draftRaw=(el.innerText||el.value||'');"
        "const normDraft=ulgNormalizeComposerText(draftRaw);"
        f"const stale={stale_js};"
        "const composerEmpty=ulgComposerDomEmpty(el);"
        "if(!composerEmpty&&!stale){"
        "const writer=String(g.ourDraftWriter||'').trim();"
        "const sig=ulgNormalizeComposerText(String(g.ourDraftSig||''));"
        "if(normDraft===normLine&&expectWriter&&writer===expectWriter&&sig===normDraft){"
        "return{ok:true,sig:normDraft,used_existing_draft:true,phase:'inserted',inserted:true,pasted:true};"
        "}"
        "if(normDraft!==normLine){"
        "return{ok:false,reason:'composer_not_empty',draft:draftRaw,phase:'foreign_draft',inserted:false,pasted:false};}"
        "if(expectWriter&&(!writer||writer!==expectWriter)){"
        "return{ok:false,reason:'composer_not_empty',draft:draftRaw,phase:'foreign_draft',inserted:false,pasted:false};}"
        "}"
        "if(el.editor&&el.editor.commands&&typeof el.editor.commands.setContent==='function'){"
        "try{el.editor.commands.setContent(ulgPlainHtml(line));}catch(e){"
        "return{ok:false,reason:'insertion_failed',phase:'insertion_failed',inserted:false,pasted:false,send_clicked:false};}"
        "}else if(el.tagName==='TEXTAREA'||el.tagName==='INPUT'){"
        "el.value=line;el.dispatchEvent(new Event('input',{bubbles:true}));"
        "}else if(el.pmViewDesc){"
        "return{ok:false,reason:'work_native_unsupported',phase:'insertion_failed',inserted:false,pasted:false,send_clicked:false};"
        "}else{"
        "return{ok:false,reason:'insertion_failed',phase:'insertion_failed',inserted:false,pasted:false,send_clicked:false};}"
        "let after=ulgNormalizeComposerText(el.innerText||el.value||'');"
        "if(after!==normLine){"
        "after=ulgNormalizeComposerText(el.innerText||el.value||'');"
        "if(after!==normLine){"
        "return{ok:false,reason:'composer_text_mismatch',send_clicked:false,phase:'inserted',inserted:true,pasted:true};}"
        "}"
        "g.ourDraftSig=after;"
        "g.ourDraftWriter=expectWriter;"
        "return{ok:true,sig:after,phase:'inserted',inserted:true,pasted:true};})()"
    )


def _click_send_own_draft_body(
    selector: str, *, draft_writer: str = "", abort_nonce: str = ""
) -> str:
    """Send only when composer draft matches this writer's ourDraftSig.

    Uses the same bounded ``ulgComposerSendRoot`` ancestor walk and
    ``ulgFindSendBtn`` eligible-control filter as ``_click_send_body``.
    """
    find = _find_composer_js(selector)
    btn_sels = json.dumps(list(_SEND_BTN_SELECTORS))
    expect_writer = json.dumps(str(draft_writer or ""))
    nonce_js = json.dumps(str(abort_nonce))
    return (
        f"(()=>{{{_ULG_NORM_COMPOSER_JS}"
        f"const el=({find});"
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
        "const sig=ulgNormalizeComposerText(String(g.ourDraftSig||''));"
        "const draft=ulgNormalizeComposerText(el.innerText||el.value||'');"
        "if(expectWriter&&( !writer||writer!==expectWriter))"
        "{cleanup();return{ok:false,reason:'foreign_draft',send_clicked:false,draft,sig,writer};}"
        "if(!sig||draft!==sig)"
        "{cleanup();return{ok:false,reason:'draft_not_ours',send_clicked:false,draft,sig};}"
        "if(sendGivenUp())"
        "{cleanup();return{ok:false,reason:'cdp_eval_timeout',send_clicked:false};}"
        "const root=ulgComposerSendRoot(el);"
        "if(!root){cleanup();return{ok:false,reason:'no_send_button',send_clicked:false};}"
        f"const btnSels={btn_sels};"
        "const btn=ulgFindSendBtn(root,el,btnSels);"
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

    Ancestor scope comes from ``ulgComposerSendRoot`` (≤8 parents, no body/html,
    no foreign form/textarea/input/second composer). A nearest Stop control
    anchors that root so an outer Send is not selected. When no Send or Stop
    is mounted yet, the same bounded resolver is polled until the page clock
    deadline; the first accepted root is then frozen and only its enabled
    Send is polled. ``ulgFindSendBtn`` rejects Stop-labelled controls and
    limits generic ``type=submit`` to the composer's own ``<form>``.

    Returns a ``(async () => …)()`` expression (Promise) for CDP ``Runtime.evaluate``
    with ``awaitPromise=True``. Python must stamp ``_composer_send_permit_expression``
    before dispatch. Final ``no_send_button`` includes a bounded ``census`` when
    polling exhausts without a click.
    """
    find = _find_composer_js(selector)
    lj = json.dumps(_dom_paste_text(line, preserve_newlines=preserve_newlines))

    stale_js = stale_wake_lines_js("t")
    btn_sels = json.dumps(list(_SEND_BTN_SELECTORS))
    wait_ceiling = int(SEND_BTN_WAIT_CEILING_MS)
    poll_interval = int(SEND_BTN_POLL_INTERVAL_MS)
    btn_cap = int(SEND_BTN_CENSUS_BUTTONS_CAP)
    attr_cap = int(SEND_BTN_CENSUS_ATTR_CAP)
    nonce_js = json.dumps(str(abort_nonce))
    return (
        f"(async ()=>{{{_ULG_NORM_COMPOSER_JS}"
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
        f"const el=({find});"
        "if(!el){cleanup();return{ok:false,reason:'no_composer',send_clicked:false};}"
        f"const line={lj};"
        "const normLine=ulgNormalizeComposerText(line);"
        "let normText=ulgNormalizeComposerText(el.innerText||el.value||'');"
        f"const wakeStale=(t)=>{stale_js};"
        "const composerEmpty=ulgComposerDomEmpty(el);"
        "if(!composerEmpty&&(!normText||(normText!==normLine&&!wakeStale(normText)))){"
        "normText=ulgNormalizeComposerText(el.innerText||el.value||'');"
        "if(!composerEmpty&&(!normText||(normText!==normLine&&!wakeStale(normText)))){"
        "cleanup();return{ok:false,reason:'composer_text_mismatch',send_clicked:false};}}"
        f"const btnSels={btn_sels};"
        f"const waitCeilingMs={wait_ceiling};"
        f"const pollIntervalMs={poll_interval};"
        "const t0=Date.now();"
        "const deadline=t0+waitCeilingMs;"
        "let root=null;"
        "while(!root&&Date.now()<deadline&&!sendGivenUp()){"
        "root=ulgComposerSendRoot(el);"
        "if(root)break;"
        "await new Promise((r)=>setTimeout(r,pollIntervalMs));"
        "}"
        "if(sendGivenUp()){cleanup();return{ok:false,reason:'cdp_eval_timeout',send_clicked:false};}"
        "if(!root){cleanup();return{ok:false,reason:'no_send_button',send_clicked:false};}"
        "const findSendBtn=()=>ulgFindSendBtn(root,el,btnSels);"
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
    body = body.replace("%WATERMARK%", json.dumps(watermark or ""))
    return body.replace("%STALE_WAKE_JS%", stale_wake_lines_js("draft"))


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


def _next_cdp_msg_id(ws: Any) -> int:
    n = int(getattr(ws, "_ulg_cdp_seq", 0)) + 1
    setattr(ws, "_ulg_cdp_seq", n)
    return n


async def _mark_composer_send_abort(ws: Any, nonce: str) -> None:
    if not nonce:
        return
    try:
        mid = _next_cdp_msg_id(ws)
        await ws.send(
            json.dumps(
                {
                    "id": mid,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": _composer_send_abort_expression(nonce),
                        "awaitPromise": False,
                        "returnByValue": True,
                    },
                }
            )
        )
        await asyncio.wait_for(_cdp_response(ws, mid), 1.0)
    except Exception:
        return


async def _cdp_eval_connected(
    ws: Any,
    expression: str,
    *,
    await_promise: bool = False,
    timeout_s: float = 8.0,
) -> Any:
    """Evaluate on an already-open CDP websocket (caller owns lifecycle)."""
    mid = _next_cdp_msg_id(ws)
    await ws.send(
        json.dumps(
            {
                "id": mid,
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
        raw = await asyncio.wait_for(_cdp_response(ws, mid), timeout_s)
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
        return await _cdp_eval_connected(
            ws,
            expression,
            await_promise=await_promise,
            timeout_s=timeout_s,
        )


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
    if value.get("used_existing_draft"):
        return {"ok": True, "used_existing_draft": True}
    return {"ok": True}


async def dom_click_send(
    ws_url: str,
    *,
    selector: str,
    line: str,
    preserve_newlines: bool = False,
    ws: Any | None = None,
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
    eval_fn = _cdp_eval_connected if ws is not None else _cdp_eval
    target = ws if ws is not None else ws_url
    try:
        try:
            await eval_fn(
                target,
                _composer_send_permit_expression(nonce, page_budget_ms),
                await_promise=False,
                timeout_s=timeout_s,
            )
        except TimeoutError:
            return {"ok": False, "reason": "cdp_eval_timeout", "send_clicked": False}
        _mark_click_dispatched()
        value = await eval_fn(
            target,
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
    work_native_insert: bool = False,
    expected_conversation_url: str = "",
) -> dict[str, Any]:
    """Insert once via DOM; send via async send-button poll (no Enter, no focus()).

    Calls ``dom_set_composer_text`` exactly once, then one send click. On send
    failure the return keeps ``reason``, ``clicked_unknown``, ``retryable``, and
    ``census``. An inserted draft that failed the click is ``phase='inserted'``
    or ``phase='send_unknown'`` with ``pasted`` true. This function does not
    click a second time.
    """
    if work_native_insert:
        from cursor_bridge.cdp_composer_input import cdp_work_native_insert_text

        probe = await _cdp_eval(
            ws_url, work_native_unsupported_probe_js(expected_url=expected_conversation_url)
        )
        if not isinstance(probe, dict):
            return {
                "ok": False,
                "reason": "work_native_unsupported",
                "pasted": False,
                "inserted": False,
                "phase": "insertion_failed",
            }
        if probe.get("eligible") is not True:
            reason = str(probe.get("reason") or "work_native_unsupported")
            if reason in ("work_dom_api_available", "no_composer"):
                ins = await dom_set_composer_text(
                    ws_url,
                    selector=selector,
                    text=line,
                    preserve_newlines=preserve_newlines,
                    draft_writer=draft_writer,
                )
            elif reason in (
                "work_native_unsupported",
                "url_mismatch",
                "ambiguous_work_composer",
            ):
                return {
                    "ok": False,
                    "reason": reason,
                    "pasted": False,
                    "inserted": False,
                    "phase": "insertion_failed",
                }
            else:
                ins = await dom_set_composer_text(
                    ws_url,
                    selector=selector,
                    text=line,
                    preserve_newlines=preserve_newlines,
                    draft_writer=draft_writer,
                )
        else:
            ins = await cdp_work_native_insert_text(
                ws_url,
                text=line,
                expected_url=expected_conversation_url,
                draft_writer=draft_writer,
                preserve_newlines=preserve_newlines,
            )
        if not isinstance(ins, dict):
            return {
                "ok": False,
                "reason": "insertion_failed",
                "pasted": False,
                "inserted": False,
            }
    else:
        ins = await dom_set_composer_text(
            ws_url,
            selector=selector,
            text=line,
            preserve_newlines=preserve_newlines,
            draft_writer=draft_writer,
        )
    if not ins.get("ok"):
        return ins
    if ins.get("used_existing_draft"):
        send = await dom_click_send_own_draft(
            ws_url, selector=selector, draft_writer=draft_writer
        )
    else:
        send = await dom_click_send(
            ws_url, selector=selector, line=line, preserve_newlines=preserve_newlines
        )
    if not send.get("ok"):
        reason = str(send.get("reason") or "send_failed")
        uncertain = bool(send.get("clicked_unknown")) or reason in {
            "cdp_eval_timeout",
            "clicked_unknown",
        }
        out: dict[str, Any] = {
            "ok": False,
            "reason": reason,
            "code": reason,
            "send_fallback_clicked": False,
            "send_clicked": bool(send.get("send_clicked")),
            "inserted": True,
            "pasted": True,
            "delivered": False,
            "phase": "send_unknown" if uncertain else "inserted",
        }
        if send.get("clicked_unknown"):
            out["clicked_unknown"] = True
        if uncertain or send.get("retryable") is False:
            out["retryable"] = False
        if send.get("census") is not None:
            out["census"] = send["census"]
        return out
    return {
        "ok": True,
        "send_fallback_clicked": bool(send.get("send_clicked")),
        "send_clicked": bool(send.get("send_clicked")),
        "inserted": True,
        "pasted": True,
        "phase": "send_clicked",
    }


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
    "composer_text_semantic_equals",
    "composer_draft_sha256_hex",
    "composer_selector",
    "normalize_composer_text",
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
