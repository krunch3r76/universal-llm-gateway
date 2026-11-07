"""Bounded send-button wait and no_send_button census (_click_send_body)."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import websockets
from cursor_bridge.dom_composer_paste import (
    SEND_BTN_CDP_EVAL_TIMEOUT_S,
    SEND_BTN_CENSUS_ATTR_CAP,
    SEND_BTN_PAGE_TIMEOUT_MARGIN_MS,
    SEND_BTN_POLL_INTERVAL_MS,
    SEND_BTN_WAIT_CEILING_MS,
    _click_send_body,
    _click_send_own_draft_body,
    _composer_send_permit_expression,
    dom_insert_and_send,
)


def _abort_nonce_from_click_js(js: str) -> str | None:
    match = re.search(r"const abortNonce=(\"(?:\\.|[^\"\\])*\")", js)
    if not match:
        return None
    return str(json.loads(match.group(1)))


def _run_click_send_js(
    js: str,
    *,
    composer_text: str,
    send_appears_after_searches: int | None = 1,
    send_appears_after_ms: int | None = None,
    buttons: list[dict[str, Any]] | None = None,
    async_js: bool = True,
    abort_nonces: dict[str, bool] | None = None,
    perf_jump_after_calls: int = 0,
    permit_deadline_offset_ms: int | None = None,
    stamp_via_mark_expression: bool = False,
) -> dict[str, Any]:
    """Evaluate generated click-send JS in Node with a minimal DOM mock."""
    if send_appears_after_searches is None:
        appear_after = -1
    else:
        appear_after = int(send_appears_after_searches)
    appear_ms = "null" if send_appears_after_ms is None else int(send_appears_after_ms)
    btn_spec = json.dumps(buttons or [])
    abort_spec = json.dumps(abort_nonces or {})
    perf_jump = int(perf_jump_after_calls)
    abort_nonce = _abort_nonce_from_click_js(js)
    if permit_deadline_offset_ms is None:
        permit_deadline_offset_ms = 600_000
    permit_offset = int(permit_deadline_offset_ms)
    if abort_nonce is None:
        permit_setup = ""
    elif stamp_via_mark_expression:
        permit_stamp = _composer_send_permit_expression(
            abort_nonce, max(0, permit_offset)
        )
        permit_setup = f"{permit_stamp};"
    else:
        permit_setup = (
            f"window.__ulgOperatorActivity.composerSendPermits[{json.dumps(abort_nonce)}] = "
            f"performance.now() + {permit_offset};"
        )
    harness = f"""
const appearAfter = {appear_after};
const appearAfterMs = {appear_ms};
const composerText = {json.dumps(composer_text)};
const extraButtons = {btn_spec};
let sendSearchCount = 0;
let sendVisibleByTimer = appearAfterMs === null;

class MockEl {{
  constructor(tag, opts = {{}}) {{
    this.tagName = tag.toUpperCase();
    this.innerText = opts.innerText ?? '';
    this.disabled = opts.disabled ?? false;
    this._attrs = opts.attrs ?? {{}};
    this._rect = opts.rect ?? {{ width: 10, height: 10 }};
    this._parent = opts.parent ?? null;
    this._form = opts.form ?? null;
    this._children = opts.children ?? [];
    this.clicked = false;
    this._clickCount = 0;
  }}
  getAttribute(name) {{
    return this._attrs[name] ?? null;
  }}
  hasAttribute(name) {{
    return Object.prototype.hasOwnProperty.call(this._attrs, name);
  }}
  getBoundingClientRect() {{
    return this._rect;
  }}
  closest(sel) {{
    if (sel === 'form') return this._form;
    return null;
  }}
  get parentElement() {{
    return this._parent;
  }}
  querySelector(sel) {{
    const isSendSel =
      sel.includes('Send') || sel.includes('send-button') || sel.includes('submit');
    if (isSendSel) {{
      sendSearchCount += 1;
      if (appearAfterMs !== null) {{
        return sendVisibleByTimer ? this._sendBtn : null;
      }}
      if (appearAfter < 0 || sendSearchCount < appearAfter) return null;
      return this._sendBtn;
    }}
    return null;
  }}
  querySelectorAll(sel) {{
    const out = [];
    const walk = (node) => {{
      if (!node) return;
      if (sel === '[contenteditable="true"]' && node.isContentEditable) out.push(node);
      if (
        sel === 'button,[role=button]'
        && (node.tagName === 'BUTTON' || (node.getAttribute && node.getAttribute('role') === 'button'))
      ) {{
        out.push(node);
      }}
      for (const c of node._children || []) walk(c);
    }};
    walk(this);
    return out;
  }}
  get isContentEditable() {{
    return String(this._attrs.contenteditable || '').toLowerCase() === 'true';
  }}
  get outerHTML() {{
    return '<form mock="1"></form>';
  }}
  click() {{
    this.clicked = true;
    this._clickCount += 1;
  }}
}}

const sendBtn = new MockEl('button', {{
  attrs: {{ 'aria-label': 'Send', type: 'submit' }},
}});
const form = new MockEl('form', {{ children: extraButtons.map((b) => new MockEl('button', {{
  attrs: b,
  rect: b.non_zero_rect === false ? {{ width: 0, height: 0 }} : {{ width: 10, height: 10 }},
}})) }});
form._sendBtn = sendBtn;
const composer = new MockEl('div', {{
  innerText: composerText,
  form,
  parent: form,
}});
form.querySelector = form.querySelector.bind(form);
form._sendBtn = sendBtn;

global.document = {{
  visibilityState: 'visible',
  hasFocus: () => true,
  querySelector: (sel) => (sel.includes('Prompt') || sel.includes('contenteditable') ? composer : null),
}};
global.window = {{ __ulgOperatorActivity: {{ composerSendAbortNonces: {abort_spec}, composerSendPermits: {{}} }} }};
global.HTMLTextAreaElement = {{ prototype: {{}} }};
global.HTMLInputElement = {{ prototype: {{}} }};
if ({perf_jump} > 0) {{
  let perfCalls = 0;
  const realNow = performance.now.bind(performance);
  global.performance = {{
    now() {{
      perfCalls += 1;
      if (perfCalls > {perf_jump}) return realNow() + 1e15;
      return realNow();
    }},
  }};
}}

if (appearAfterMs !== null) {{
  setTimeout(() => {{ sendVisibleByTimer = true; }}, appearAfterMs);
}}

{permit_setup}

(async () => {{
  const result = {"await " + js if async_js else js};
  result.__sendSearchCount = sendSearchCount;
  result.__sendClicked = !!(sendBtn && sendBtn.clicked);
  console.log(JSON.stringify(result));
}})().catch((err) => {{
  console.error(err);
  process.exit(1);
}});
"""
    proc = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    out = json.loads(proc.stdout.strip())
    return out


def _run_work_shape_click_send_js(
    js: str,
    *,
    composer_text: str,
    draft_writer: str = "",
    include_send_prompt: bool = True,
    send_disabled: bool = False,
    stop_only: bool = False,
    extra_editable_in_editor_col: bool = False,
    misplaced_send_in_editor_col: bool = False,
    send_on_document_body: bool = False,
    detach_from_body: bool = False,
) -> dict[str, Any]:
    """ChatGPT Work layout: editor shell has no buttons; footer is several ancestors up."""
    writer = json.dumps(draft_writer)
    abort_nonce = _abort_nonce_from_click_js(js)
    permit_setup = ""
    if abort_nonce:
        permit_setup = (
            f"window.__ulgOperatorActivity.composerSendPermits[{json.dumps(abort_nonce)}] = "
            "performance.now() + 600000;"
        )
    if send_on_document_body and detach_from_body:
        body_block = """
const bodyEl = new MockEl('body', { children: [] });
const bodySend = new MockEl('button', {
  attrs: { 'aria-label': 'Send prompt', type: 'button' },
});
bodyEl._children = [bodySend];
bodySend._parent = bodyEl;
"""
        doc_body = "bodyEl"
    elif send_on_document_body:
        body_block = """
const bodyEl = new MockEl('body', { children: [] });
layout._parent = bodyEl;
const bodySend = new MockEl('button', {
  attrs: { 'aria-label': 'Send prompt', type: 'button' },
});
bodyEl._children = [layout, bodySend];
bodySend._parent = bodyEl;
"""
        doc_body = "bodyEl"
    else:
        body_block = "const bodyEl = null;"
        doc_body = "null"
    harness = f"""
const composerText = {json.dumps(composer_text)};
const draftWriter = {writer};
class MockEl {{
  constructor(tag, opts = {{}}) {{
    this.tagName = tag.toUpperCase();
    this.innerText = opts.innerText ?? '';
    this._attrs = opts.attrs ?? {{}};
    this._children = opts.children ?? [];
    this._parent = opts.parent ?? null;
    this.editor = opts.editor ?? null;
    this.clicked = false;
    this._clickCount = 0;
    this.disabled = opts.disabled ?? false;
    for (const c of this._children) c._parent = this;
  }}
  get isContentEditable() {{
    return String(this._attrs.contenteditable || '').toLowerCase() === 'true';
  }}
  getAttribute(name) {{ return this._attrs[name] ?? null; }}
  hasAttribute(name) {{ return Object.prototype.hasOwnProperty.call(this._attrs, name); }}
  get parentElement() {{ return this._parent; }}
  closest(sel) {{ return sel === 'form' ? null : null; }}
  getBoundingClientRect() {{ return {{ width: 10, height: 10 }}; }}
  matches(sel) {{
    if (sel.includes('[contenteditable="true"]')) {{
      return String(this._attrs.contenteditable || '').toLowerCase() === 'true';
    }}
    return false;
  }}
  querySelector(sel) {{
    const match = (node) => {{
      if (!node) return null;
      if (sel === 'button' && node.tagName === 'BUTTON') return node;
      if (sel === '[role=button]' && node.getAttribute && node.getAttribute('role') === 'button') {{
        return node;
      }}
      if (node._matchesSendSel && node._matchesSendSel(sel)) return node;
      for (const c of node._children || []) {{
        const hit = match(c);
        if (hit) return hit;
      }}
      return null;
    }};
    return match(this);
  }}
  querySelectorAll(sel) {{
    const out = [];
    const walk = (node) => {{
      if (!node) return;
      if (sel === '[contenteditable="true"]' && node.isContentEditable) out.push(node);
      if (
        sel === 'button,[role=button]'
        && (node.tagName === 'BUTTON' || (node.getAttribute && node.getAttribute('role') === 'button'))
      ) {{
        out.push(node);
      }}
      for (const c of node._children || []) walk(c);
    }};
    walk(this);
    return out;
  }}
  get outerHTML() {{ return '<div class="work-mock"></div>'; }}
  click() {{
    this.clicked = true;
    this._clickCount += 1;
  }}
}}
function buttonMatches(sel) {{
  const btn = this;
  if (!btn || btn.tagName !== 'BUTTON') return false;
  if (sel.includes(':not([disabled])') && btn.disabled) return false;
  let m = sel.match(/aria-label="([^"]+)"/);
  if (m) return btn.getAttribute('aria-label') === m[1];
  m = sel.match(/data-testid="([^"]+)"/);
  if (m) return btn.getAttribute('data-testid') === m[1];
  if (sel.includes('type="submit"')) return btn.getAttribute('type') === 'submit';
  return false;
}}
MockEl.prototype._matchesSendSel = buttonMatches;
const sendPrompt = new MockEl('button', {{
  attrs: {{ 'aria-label': 'Send prompt', type: 'button' }},
  disabled: {json.dumps(send_disabled)},
}});
const stopBtn = new MockEl('button', {{ attrs: {{ 'aria-label': 'Stop', type: 'button' }} }});
const footerChildren = [stopBtn];
if ({json.dumps(include_send_prompt and not stop_only)}) footerChildren.push(sendPrompt);
const footer = new MockEl('div', {{ attrs: {{ class: 'ComposerLayoutFooter' }}, children: footerChildren }});
const editorShell = new MockEl('div', {{ attrs: {{ class: 'text-size-chat LegacyExpandableEditor' }} }});
const composer = new MockEl('div', {{
  attrs: {{ contenteditable: 'true', 'aria-label': 'Work with ChatGPT', role: 'textbox' }},
  innerText: composerText,
  parent: editorShell,
}});
editorShell._children = [composer];
const editorColChildren = [editorShell];
if ({json.dumps(extra_editable_in_editor_col)}) {{
  editorColChildren.push(new MockEl('div', {{
    attrs: {{ contenteditable: 'true', 'aria-label': 'foreign composer' }},
    innerText: 'other',
  }}));
}}
let misplacedSend = null;
if ({json.dumps(misplaced_send_in_editor_col)}) {{
  misplacedSend = new MockEl('button', {{
    attrs: {{ 'aria-label': 'Send prompt', type: 'button' }},
  }});
  editorColChildren.push(misplacedSend);
}}
const editorCol = new MockEl('div', {{ attrs: {{ class: 'editor-col' }}, children: editorColChildren }});
const layout = new MockEl('div', {{ attrs: {{ class: 'composer-layout' }}, children: [editorCol, footer] }});
editorShell._parent = editorCol;
editorCol._parent = layout;
footer._parent = layout;
composer._parent = editorShell;
{body_block}
global.document = {{
  visibilityState: 'visible',
  hasFocus: () => true,
  querySelector: (sel) => (sel.includes('contenteditable') ? composer : null),
  body: {doc_body},
}};
global.window = {{
  __ulgOperatorActivity: {{
    ourDraftSig: composerText,
    ourDraftWriter: draftWriter,
    composerSendPermits: {{}},
    composerSendAbortNonces: {{}},
  }},
}};
global.HTMLTextAreaElement = {{ prototype: {{}} }};
global.HTMLInputElement = {{ prototype: {{}} }};
{permit_setup}
(async () => {{
  const result = {"await " + js if js.lstrip().startswith("(async") else js};
  result.__sendClicked = sendPrompt.clicked;
  result.__stopClicked = stopBtn.clicked;
  result.__sendClickCount = sendPrompt._clickCount || 0;
  result.__misplacedSendClickCount = misplacedSend ? misplacedSend._clickCount : 0;
  console.log(JSON.stringify(result));
}})().catch((err) => {{ console.error(err); process.exit(1); }});
"""
    proc = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(
    subprocess.run(["which", "node"], capture_output=True).returncode != 0,
    reason="node not installed",
)
def test_work_footer_send_prompt_clicks_once() -> None:
    """Production JS on Work-shaped DOM: Send prompt in ancestor footer, not editor shell."""
    js = _click_send_body(
        'div.ProseMirror[contenteditable="true"]', "wake line after idle", abort_nonce="work-n"
    )
    result = _run_work_shape_click_send_js(js, composer_text="wake line after idle")
    assert result["ok"] is True
    assert result["send_clicked"] is True
    assert result["__sendClickCount"] == 1
    assert result["__stopClicked"] is False


@pytest.mark.skipif(
    subprocess.run(["which", "node"], capture_output=True).returncode != 0,
    reason="node not installed",
)
def test_work_footer_owned_draft_send_prompt_clicks_once() -> None:
    js = _click_send_own_draft_body(
        'div.ProseMirror[contenteditable="true"]',
        draft_writer="orion-wake",
        abort_nonce="work-own",
    )
    result = _run_work_shape_click_send_js(
        js,
        composer_text="owned draft line",
        draft_writer="orion-wake",
    )
    assert result["ok"] is True
    assert result["send_clicked"] is True
    assert result["__sendClickCount"] == 1
    assert result["__stopClicked"] is False


@pytest.mark.skipif(
    subprocess.run(["which", "node"], capture_output=True).returncode != 0,
    reason="node not installed",
)
def test_work_footer_stop_only_no_send_button() -> None:
    js = _click_send_body(
        'div.ProseMirror[contenteditable="true"]', "wake", abort_nonce="stop-only"
    )
    result = _run_work_shape_click_send_js(
        js, composer_text="wake", stop_only=True, include_send_prompt=False
    )
    assert result["ok"] is False
    assert result["reason"] == "no_send_button"
    assert result["__sendClickCount"] == 0
    assert result["__stopClicked"] is False


@pytest.mark.skipif(
    subprocess.run(["which", "node"], capture_output=True).returncode != 0,
    reason="node not installed",
)
def test_work_footer_disabled_send_prompt_not_clicked() -> None:
    js = _click_send_body(
        'div.ProseMirror[contenteditable="true"]', "wake", abort_nonce="dis-send"
    )
    result = _run_work_shape_click_send_js(
        js, composer_text="wake", send_disabled=True
    )
    assert result["ok"] is False
    assert result["reason"] == "no_send_button"
    assert result["__sendClickCount"] == 0


@pytest.mark.skipif(
    subprocess.run(["which", "node"], capture_output=True).returncode != 0,
    reason="node not installed",
)
def test_work_send_on_body_only_outside_composer_chain() -> None:
    js = _click_send_body(
        'div.ProseMirror[contenteditable="true"]', "wake", abort_nonce="body-only"
    )
    result = _run_work_shape_click_send_js(
        js,
        composer_text="wake",
        send_on_document_body=True,
        detach_from_body=True,
        include_send_prompt=False,
        stop_only=True,
    )
    assert result["ok"] is False
    assert result["reason"] == "no_send_button"
    assert result["__sendClickCount"] == 0


@pytest.mark.skipif(
    subprocess.run(["which", "node"], capture_output=True).returncode != 0,
    reason="node not installed",
)
def test_work_second_composer_skips_misplaced_send_not_footer() -> None:
    """Two editables skip misleading ancestor roots; footer send is not mis-clicked."""
    js = _click_send_body(
        'div.ProseMirror[contenteditable="true"]', "wake", abort_nonce="2edit"
    )
    result = _run_work_shape_click_send_js(
        js,
        composer_text="wake",
        extra_editable_in_editor_col=True,
        misplaced_send_in_editor_col=True,
    )
    assert result["ok"] is False
    assert result["reason"] == "no_send_button"
    assert result["__sendClickCount"] == 0
    assert result["__misplacedSendClickCount"] == 0
    assert result["__stopClicked"] is False


def test_send_control_found_immediately_no_extra_poll() -> None:
    js = _click_send_body('[aria-label="Prompt"][contenteditable="true"]', "hello")
    result = _run_click_send_js(
        js, composer_text="hello", send_appears_after_searches=1
    )
    assert result["ok"] is True
    assert result["send_clicked"] is True
    assert result["__sendSearchCount"] == 1


def test_send_control_appears_on_third_search() -> None:
    js = _click_send_body('[aria-label="Prompt"][contenteditable="true"]', "wake text")
    result = _run_click_send_js(
        js, composer_text="wake text", send_appears_after_searches=3
    )
    assert result["ok"] is True
    assert result["send_clicked"] is True
    assert result["__sendSearchCount"] == 3


def test_send_never_appears_census_and_bounded_wait() -> None:
    js = _click_send_body('[aria-label="Prompt"][contenteditable="true"]', "hello")
    result = _run_click_send_js(
        js,
        composer_text="hello",
        send_appears_after_searches=None,
        buttons=[
            {"aria-label": "Dictate", "type": "button"},
            {"aria-label": "Send", "type": "submit", "disabled": "true"},
        ],
    )
    assert result["ok"] is False
    assert result["reason"] == "no_send_button"
    census = result["census"]
    assert census["root_tag"] == "form"
    assert "root_outer_html" in census
    assert len(census["root_outer_html"]) <= 4000
    assert census["document_visibility_state"] == "visible"
    assert census["document_has_focus"] is True
    assert isinstance(census["buttons"], list)
    assert (
        census["elapsed_ms"]
        <= SEND_BTN_WAIT_CEILING_MS + SEND_BTN_POLL_INTERVAL_MS + 50
    )


def test_dom_insert_and_send_inserts_once(monkeypatch: pytest.MonkeyPatch) -> None:
    insert_calls = 0
    send_calls = 0

    async def fake_eval(_ws: str, expr: str, **_kw: Any) -> Any:
        nonlocal insert_calls, send_calls
        if "setContent" in expr or "textContent=line" in expr:
            insert_calls += 1
            return {"ok": True, "sig": "hello"}
        send_calls += 1
        return {"ok": True, "send_clicked": True}

    monkeypatch.setattr(
        "cursor_bridge.dom_composer_paste._cdp_eval",
        fake_eval,
    )
    import asyncio

    out = asyncio.run(
        dom_insert_and_send(
            "ws://x",
            selector='[aria-label="Prompt"][contenteditable="true"]',
            line="hello",
        )
    )
    assert out["ok"] is True
    assert insert_calls == 1
    assert send_calls == 2


@pytest.mark.asyncio
async def test_dom_click_send_logs_census_on_no_send_button(
    caplog: pytest.LogCaptureFixture,
) -> None:
    census = {"root_tag": "form", "buttons": [], "elapsed_ms": 3000}
    with patch(
        "cursor_bridge.dom_composer_paste._cdp_eval",
        new=AsyncMock(
            return_value={
                "ok": False,
                "reason": "no_send_button",
                "send_clicked": False,
                "census": census,
            }
        ),
    ):
        from cursor_bridge.dom_composer_paste import dom_click_send

        await dom_click_send(
            "ws://x",
            selector='[aria-label="Prompt"][contenteditable="true"]',
            line="hello",
        )
    assert any("no_send_button census=" in r.message for r in caplog.records)


def test_click_send_js_has_no_sync_date_spin() -> None:
    js = _click_send_body('[aria-label="Prompt"][contenteditable="true"]', "hello")
    assert "while(Date.now()-" not in js.replace(" ", "")
    assert "sleepMs" not in js


def test_cdp_eval_timeout_exceeds_send_wait_ceiling() -> None:
    assert SEND_BTN_CDP_EVAL_TIMEOUT_S > SEND_BTN_WAIT_CEILING_MS / 1000.0
    page_budget_ms = max(
        1,
        int(SEND_BTN_CDP_EVAL_TIMEOUT_S * 1000) - int(SEND_BTN_PAGE_TIMEOUT_MARGIN_MS),
    )
    assert page_budget_ms > SEND_BTN_WAIT_CEILING_MS


@pytest.mark.asyncio
async def test_page_budget_floor_for_subsecond_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cursor_bridge.dom_composer_paste as paste

    budgets: list[int] = []
    real_permit = paste._composer_send_permit_expression

    def _capture_permit(nonce: str, page_budget_ms: int) -> str:
        budgets.append(int(page_budget_ms))
        return real_permit(nonce, page_budget_ms)

    monkeypatch.setattr(paste, "_composer_send_permit_expression", _capture_permit)
    monkeypatch.setattr(
        paste,
        "_cdp_eval",
        AsyncMock(return_value={"ok": True, "send_clicked": True}),
    )
    monkeypatch.setattr(paste, "SEND_BTN_CDP_EVAL_TIMEOUT_S", 0.05)
    await paste.dom_click_send(
        "ws://x",
        selector='[aria-label="Prompt"][contenteditable="true"]',
        line="hello",
    )
    assert budgets[-1] == 1
    monkeypatch.setattr(paste, "SEND_BTN_CDP_EVAL_TIMEOUT_S", 8.0)
    await paste.dom_click_send(
        "ws://x",
        selector='[aria-label="Prompt"][contenteditable="true"]',
        line="hello",
    )
    assert budgets[-1] == 7000


@pytest.mark.asyncio
async def test_own_draft_expired_permit_does_not_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cursor_bridge.dom_composer_paste as paste

    expressions: list[str] = []
    eval_calls = 0

    async def fake_eval(
        _ws_url: str, expression: str, **_kwargs: object
    ) -> dict[str, Any]:
        nonlocal eval_calls
        eval_calls += 1
        expressions.append(expression)
        if eval_calls == 1:
            return {"ok": True}
        return {"ok": False, "reason": "cdp_eval_timeout", "send_clicked": False}

    monkeypatch.setattr(paste, "_cdp_eval", fake_eval)
    result = await paste.dom_click_send_own_draft(
        "ws://x",
        selector='[aria-label="Prompt"][contenteditable="true"]',
    )
    assert result == {
        "ok": False,
        "reason": "cdp_eval_timeout",
        "send_clicked": False,
    }
    assert len(expressions) == 2
    assert "composerSendPermits" in expressions[0]
    assert "ourDraftSig" in expressions[1]


def test_send_control_appears_after_timer_yield() -> None:
    js = _click_send_body('[aria-label="Prompt"][contenteditable="true"]', "hello")
    result = _run_click_send_js(
        js,
        composer_text="hello",
        send_appears_after_ms=300,
    )
    assert result["ok"] is True
    assert result["send_clicked"] is True


def _repo_root_from_test_file() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(Path(__file__).resolve().parent),
    )
    root = proc.stdout.strip()
    if not root:
        raise RuntimeError("git rev-parse --show-toplevel returned empty")
    return root


def _legacy_click_send_body_at(rev: str) -> Any:
    proc = subprocess.run(
        ["git", "show", f"{rev}:libs/cursor_bridge/dom_composer_paste.py"],
        capture_output=True,
        text=True,
        check=True,
        cwd=_repo_root_from_test_file(),
    )
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(proc.stdout)
        path = Path(tmp.name)
    spec = importlib.util.spec_from_file_location(f"legacy_dcp_{rev[:8]}", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    path.unlink(missing_ok=True)
    return mod._click_send_body


def test_legacy_spin_body_fails_timer_appearance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """838d2c18 sync spin blocks Node timers — same failure mode as a real page."""
    monkeypatch.chdir(tmp_path)
    legacy_fn = _legacy_click_send_body_at("838d2c18")
    legacy_js = legacy_fn('[aria-label="Prompt"][contenteditable="true"]', "hello")
    result = _run_click_send_js(
        legacy_js,
        composer_text="hello",
        send_appears_after_ms=300,
        async_js=False,
    )
    assert result["ok"] is False
    assert result["reason"] == "no_send_button"


def test_census_buttons_capped_at_twenty() -> None:
    js = _click_send_body('[aria-label="Prompt"][contenteditable="true"]', "hello")
    many = [{"aria-label": f"b{i}", "type": "button"} for i in range(50)]
    result = _run_click_send_js(
        js,
        composer_text="hello",
        send_appears_after_searches=None,
        buttons=many,
    )
    census = result["census"]
    assert len(census["buttons"]) == 20
    assert census["buttons_total"] == 50


@pytest.mark.asyncio
async def test_dom_insert_and_send_forwards_census_on_send_failure() -> None:
    census = {"root_tag": "form", "buttons": [], "buttons_total": 0, "elapsed_ms": 3000}
    call_idx = 0

    async def fake_eval(_ws: str, expr: str, **_kw: Any) -> Any:
        nonlocal call_idx
        call_idx += 1
        if call_idx == 1:
            return {"ok": True, "sig": "hello"}
        if call_idx == 2:
            return {"ok": True}
        return {
            "ok": False,
            "reason": "no_send_button",
            "send_clicked": False,
            "census": census,
        }

    with patch("cursor_bridge.dom_composer_paste._cdp_eval", new=fake_eval):
        from cursor_bridge.dom_composer_paste import dom_insert_and_send

        out = await dom_insert_and_send(
            "ws://x",
            selector='[aria-label="Prompt"][contenteditable="true"]',
            line="hello",
        )
    assert out["ok"] is False
    assert out["reason"] == "no_send_button"
    assert out["pasted"] is True
    assert out["delivered"] is False
    assert out["census"] == census


@pytest.mark.asyncio
async def test_dom_click_send_uses_await_promise() -> None:
    with patch(
        "cursor_bridge.dom_composer_paste._cdp_eval",
        new=AsyncMock(return_value={"ok": True, "send_clicked": True}),
    ) as mock_eval:
        from cursor_bridge.dom_composer_paste import dom_click_send

        await dom_click_send(
            "ws://x",
            selector='[aria-label="Prompt"][contenteditable="true"]',
            line="hello",
        )
    assert mock_eval.call_count == 2
    assert mock_eval.call_args_list[0].kwargs.get("await_promise") is False
    assert mock_eval.call_args_list[1].kwargs.get("await_promise") is True


def test_page_deadline_returns_timeout_without_click() -> None:
    js = _click_send_body(
        '[aria-label="Prompt"][contenteditable="true"]',
        "hello",
        abort_nonce="call-deadline",
    )
    result = _run_click_send_js(
        js,
        composer_text="hello",
        send_appears_after_searches=1,
        perf_jump_after_calls=1,
    )
    assert result["ok"] is False
    assert result["reason"] == "cdp_eval_timeout"
    assert result["send_clicked"] is False
    assert result["__sendClicked"] is False


def test_aborted_nonce_does_not_block_later_send() -> None:
    selector = '[aria-label="Prompt"][contenteditable="true"]'
    js_a = _click_send_body(selector, "hello", abort_nonce="call-a")
    missed = _run_click_send_js(
        js_a,
        composer_text="hello",
        send_appears_after_searches=1,
        abort_nonces={"call-a": True},
    )
    assert missed["reason"] == "cdp_eval_timeout"
    assert missed["send_clicked"] is False
    assert missed["__sendClicked"] is False

    js_b = _click_send_body(selector, "hello", abort_nonce="call-b")
    sent = _run_click_send_js(
        js_b,
        composer_text="hello",
        send_appears_after_searches=1,
        abort_nonces={"call-a": True},
    )
    assert sent["ok"] is True
    assert sent["send_clicked"] is True
    assert sent["__sendClicked"] is True


def test_census_attr_strings_capped() -> None:
    cap = SEND_BTN_CENSUS_ATTR_CAP
    long = "x" * (cap + 40)
    js = _click_send_body('[aria-label="Prompt"][contenteditable="true"]', "hello")
    result = _run_click_send_js(
        js,
        composer_text="hello",
        send_appears_after_searches=None,
        buttons=[
            {"aria-label": long, "data-testid": long, "type": long},
            {"aria-label": "Ok"},
        ],
    )
    census = result["census"]
    capped = census["buttons"][0]
    assert capped["aria_label"] == long[:cap]
    assert capped["data_testid"] == long[:cap]
    assert capped["type"] == long[:cap]
    assert len(capped["aria_label"]) == cap
    absent = census["buttons"][1]
    assert absent["data_testid"] is None
    assert absent["type"] is None


class _FakePageSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self._recv_n = 0
        self.started = asyncio.Event()

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def recv(self) -> str:
        self._recv_n += 1
        if self._recv_n == 1:
            self.started.set()
            await asyncio.sleep(30)
        return json.dumps({"id": 2, "result": {"result": {"value": {"ok": True}}}})

    async def __aenter__(self) -> _FakePageSocket:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


def _patch_page_socket(monkeypatch: pytest.MonkeyPatch, ws: _FakePageSocket) -> None:
    monkeypatch.setattr(websockets, "connect", lambda *_a, **_k: ws)


@pytest.mark.asyncio
async def test_dom_click_send_timeout_marks_nonce_and_does_not_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cursor_bridge.dom_composer_paste as paste

    ws = _FakePageSocket()
    _patch_page_socket(monkeypatch, ws)
    monkeypatch.setattr(paste, "SEND_BTN_CDP_EVAL_TIMEOUT_S", 0.05)
    result = await paste.dom_click_send(
        "ws://x",
        selector='[aria-label="Prompt"][contenteditable="true"]',
        line="hello",
    )
    assert result == {
        "ok": False,
        "reason": "cdp_eval_timeout",
        "send_clicked": False,
    }
    assert ws.sent[0]["method"] == "Runtime.evaluate"
    assert "composerSendPermits" in ws.sent[0]["params"]["expression"]
    click_evals = [
        m
        for m in ws.sent
        if m.get("method") == "Runtime.evaluate"
        and "findSendBtn" in m.get("params", {}).get("expression", "")
    ]
    assert not click_evals
    mark = ws.sent[1]
    assert mark["id"] == 2
    assert mark["method"] == "Runtime.evaluate"
    assert "composerSendAbortNonces" in mark["params"]["expression"]
    assert all(msg["method"] != "Runtime.terminateExecution" for msg in ws.sent)


class _FakePermitThenHangingClickSocket:
    _next_conn = 0

    def __init__(self) -> None:
        self.conn_id = _FakePermitThenHangingClickSocket._next_conn
        _FakePermitThenHangingClickSocket._next_conn += 1
        self.sent: list[dict[str, Any]] = []
        self.started = asyncio.Event()

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def recv(self) -> str:
        if self.conn_id == 0:
            return json.dumps({"id": 1, "result": {"result": {"value": {"ok": True}}}})
        self.started.set()
        await asyncio.sleep(30)
        return json.dumps({"id": 1, "result": {"result": {"value": {"ok": True}}}})

    async def __aenter__(self) -> _FakePermitThenHangingClickSocket:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


@pytest.mark.asyncio
async def test_click_eval_timeout_after_dispatch_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cursor_bridge.dom_composer_paste as paste

    eval_calls = 0

    async def fake_eval(
        _ws_url: str, _expression: str, **_kwargs: object
    ) -> dict[str, bool]:
        nonlocal eval_calls
        eval_calls += 1
        if eval_calls == 1:
            return {"ok": True}
        raise TimeoutError

    monkeypatch.setattr(paste, "_cdp_eval", fake_eval)
    result = await paste.dom_click_send(
        "ws://x",
        selector='[aria-label="Prompt"][contenteditable="true"]',
        line="hello",
    )
    assert result == {
        "ok": False,
        "reason": "cdp_eval_timeout",
        "send_clicked": False,
        "clicked_unknown": True,
        "retryable": False,
    }
    assert eval_calls == 2


@pytest.mark.asyncio
async def test_dom_click_send_cancel_returns_cdp_eval_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import inspect

    import cursor_bridge.dom_composer_paste as paste

    class _Sock:
        _next = 0

        def __init__(self) -> None:
            self.conn_id = _Sock._next
            _Sock._next += 1
            self.sent: list[dict[str, Any]] = []

        async def send(self, data: str) -> None:
            self.sent.append(json.loads(data))

        async def recv(self) -> str:
            if self.conn_id == 0:
                return json.dumps(
                    {"id": 1, "result": {"result": {"value": {"ok": True}}}}
                )
            await asyncio.sleep(3600)
            return json.dumps({"id": 1, "result": {"result": {"value": {}}}})

        async def __aenter__(self) -> _Sock:
            return self

        async def __aexit__(self, *_exc: object) -> bool:
            return False

    socks: list[_Sock] = []

    def _connect(*_a: object, **_k: object) -> _Sock:
        sock = _Sock()
        socks.append(sock)
        return sock

    _Sock._next = 0
    monkeypatch.setattr(websockets, "connect", _connect)
    handler_recancel = False
    task = asyncio.create_task(
        paste.dom_click_send(
            "ws://x",
            selector='[aria-label="Prompt"][contenteditable="true"]',
            line="hello",
        )
    )
    _orig_task_cancel = task.cancel

    def _tracking_task_cancel(*args: object, **kwargs: object) -> bool:
        nonlocal handler_recancel
        for frame in inspect.stack():
            if frame.filename.endswith("dom_composer_paste.py"):
                handler_recancel = True
                break
        return _orig_task_cancel(*args, **kwargs)

    task.cancel = _tracking_task_cancel  # type: ignore[method-assign]
    for _ in range(50):
        if len(socks) >= 2:
            break
        await asyncio.sleep(0.01)
    assert len(socks) >= 2
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    all_sent = [msg for sock in socks for msg in sock.sent]
    click_evals = [
        m
        for m in all_sent
        if m.get("method") == "Runtime.evaluate"
        and "findSendBtn" in m.get("params", {}).get("expression", "")
    ]
    assert click_evals
    assert any(
        msg.get("id") == 2 and "composerSendAbortNonces" in msg["params"]["expression"]
        for msg in all_sent
    )
    assert handler_recancel is True


@pytest.mark.skipif(
    subprocess.run(["which", "node"], capture_output=True).returncode != 0,
    reason="node not installed",
)
def test_permit_expired_before_click_script_no_click() -> None:
    js = _click_send_body('[aria-label="Prompt"][contenteditable="true"]', "hello")
    result = _run_click_send_js(
        js,
        composer_text="hello",
        send_appears_after_searches=1,
        permit_deadline_offset_ms=-1,
    )
    assert result["ok"] is False
    assert result["reason"] == "cdp_eval_timeout"
    assert result["send_clicked"] is False
    assert result["__sendClicked"] is False


@pytest.mark.skipif(
    subprocess.run(["which", "node"], capture_output=True).returncode != 0,
    reason="node not installed",
)
def test_mark_then_click_valid_permit_aborted_nonce_no_click() -> None:
    js = _click_send_body(
        '[aria-label="Prompt"][contenteditable="true"]',
        "hello",
        abort_nonce="permit-still-valid",
    )
    result = _run_click_send_js(
        js,
        composer_text="hello",
        send_appears_after_searches=1,
        abort_nonces={"permit-still-valid": True},
        stamp_via_mark_expression=True,
    )
    assert result["ok"] is False
    assert result["reason"] == "cdp_eval_timeout"
    assert result["send_clicked"] is False
    assert result["__sendClicked"] is False
