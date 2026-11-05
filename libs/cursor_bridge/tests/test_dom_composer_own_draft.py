"""Submit-without-text must match ourDraftSig in generated JS."""

import json
import os
import shutil
import subprocess
import textwrap

import pytest
from cursor_bridge.dom_composer_paste import (
    _click_send_body,
    _click_send_own_draft_body,
    _set_composer_body,
    composer_text_semantic_equals,
    dom_insert_and_send,
    normalize_composer_text,
    operator_busy_probe_js,
)


def test_operator_busy_probe_uses_ordered_selectors() -> None:
    js = operator_busy_probe_js(
        selector="[role='textbox'], [contenteditable='true']",
    )
    assert "for (const s of sels)" in js
    assert "document.querySelector(s)" in js
    assert "document.querySelector(" not in js.split("for (const s of sels)")[0]


def test_draft_sig_writes_clear_our_draft_writer() -> None:
    """Marking or clearing ourDraftSig also sets ourDraftWriter to empty."""
    from cursor_bridge.cdp_composer_input import (
        _mark_our_draft_sig_js,
        clear_our_composer_draft_js,
    )
    from cursor_bridge.dom_composer_paste import clear_composer_draft_dom_no_focus_js

    marked = _mark_our_draft_sig_js("[contenteditable='true']", "hello")
    cleared = clear_our_composer_draft_js(
        selector="[contenteditable='true']", watermark="WAKE"
    )
    no_focus = clear_composer_draft_dom_no_focus_js("[contenteditable='true']")
    for js in (marked, cleared, no_focus):
        assert "ourDraftSig" in js
        assert "g.ourDraftWriter='';" in js


def test_click_send_own_draft_checks_sig() -> None:
    js = _click_send_own_draft_body("[contenteditable='true']")
    assert "ourDraftSig" in js
    assert "draft_not_ours" in js


def test_composer_text_semantic_equals_nfc_nbsp_and_newlines() -> None:
    assert composer_text_semantic_equals("café", "caf\u00e9")
    assert composer_text_semantic_equals("a\u00a0b", "a b")
    assert composer_text_semantic_equals("line1\nline2", "line1\nline2")
    assert composer_text_semantic_equals("  spaced  ", "spaced")
    assert not composer_text_semantic_equals("alpha", "beta")
    assert normalize_composer_text("x\r\ny") == "x\ny"


def test_dom_js_includes_semantic_normalizer() -> None:
    set_js = _set_composer_body("[contenteditable='true']", "hello", draft_writer="w")
    click_js = _click_send_body("[contenteditable='true']", "hello")
    assert "ulgNormalizeComposerText" in set_js
    assert "ulgPlainHtml" in set_js
    assert "setContent(line)" not in set_js
    assert "ulgNormalizeComposerText" in click_js


_FIXTURE_JS = textwrap.dedent(
    r"""
    const fs = require('fs');
    const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
    function decode(text) {
      return text
        .replace(/&nbsp;/g, '\u00a0')
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'")
        .replace(/&amp;/g, '&');
    }
    function htmlToInnerText(html) {
      const src = String(html);
      let out = '';
      let i = 0;
      while (i < src.length) {
        if (src[i] === '<') {
          const end = src.indexOf('>', i);
          if (end < 0) { out += src.slice(i); break; }
          const raw = src.slice(i + 1, end).trim().toLowerCase();
          const closing = raw.startsWith('/');
          const name = raw.replace(/^\//, '').split(/\s+/)[0];
          if (name === 'br' || (closing && (name === 'p' || name === 'div'))) {
            if (!out.endsWith('\n')) out += '\n';
          }
          i = end + 1;
          continue;
        }
        const next = src.indexOf('<', i);
        const chunk = src.slice(i, next < 0 ? src.length : next);
        out += decode(chunk).replace(/[ \t\r\n\f]+/g, ' ');
        i = next < 0 ? src.length : next;
      }
      return out.replace(/^\n/, '').replace(/\n$/, '');
    }
    function norm(raw) {
      let s = String(raw ?? '').normalize('NFC');
      s = s.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
      s = s.replace(/\u00a0/g, ' ').replace(/\u202f/g, ' ');
      return s.trim();
    }
    function makeEl(initial) {
      const el = {
        tagName: 'DIV',
        _text: initial || '',
        _setCalls: [],
        editor: { commands: { setContent(value) {
          if (typeof value !== 'string') throw new Error('setContent rejected ' + typeof value);
          el._text = htmlToInnerText(value);
          el._setCalls.push(value);
        }}},
        get innerText() { return el._text; },
        set innerText(v) { el._text = String(v); },
        get value() { return el._text; },
        set textContent(v) { el._text = String(v); },
        dispatchEvent() {},
        closest() { return null; },
        parentElement: null,
      };
      return el;
    }
    global.document = {
      querySelector(sel) {
        const s = String(sel);
        if (s.includes('Send') || s.includes('send-button') || s.includes('submit')) {
          return global.__button || null;
        }
        return global.__el;
      },
      querySelectorAll() { return []; },
      visibilityState: 'visible',
      hasFocus() { return false; },
    };
    global.window = { __ulgOperatorActivity: {} };
    (async () => {
      const results = [];
      for (const item of spec.cases) {
        global.window.__ulgOperatorActivity = item.activity || {};
        if (item.kind === 'old_vs_new') {
          const oldEl = makeEl('');
          oldEl.editor.commands.setContent(item.line);
          const el = makeEl('');
          global.__el = el;
          const ret = eval(item.setter);
          results.push({
            id: item.id,
            oldMatch: norm(oldEl.innerText) === norm(item.line),
            newOk: !!ret.ok,
            newPhase: ret.phase || '',
            newMatch: norm(el.innerText) === norm(item.line),
            calls: el._setCalls.length,
          });
        } else if (item.kind === 'draft') {
          const el = makeEl(item.draft);
          global.__el = el;
          const before = el.innerText;
          const ret = eval(item.setter);
          results.push({
            id: item.id,
            ok: !!ret.ok,
            reason: ret.reason || '',
            used: !!ret.used_existing_draft,
            unchanged: el.innerText === before,
            calls: el._setCalls.length,
          });
        } else if (item.kind === 'click') {
          if (item.nonce) {
            global.window.__ulgOperatorActivity.composerSendPermits = {};
            global.window.__ulgOperatorActivity.composerSendPermits[item.nonce] =
              performance.now() + 60000;
          }
          const el = makeEl(item.draft);
          global.__el = el;
          let clicks = 0;
          global.__button = { click() { clicks += 1; } };
          const ret = await eval(item.clicker);
          results.push({
            id: item.id,
            ok: !!ret.ok,
            reason: ret.reason || '',
            clicks,
          });
        }
      }
      process.stdout.write(JSON.stringify(results));
    })().catch((err) => { console.error(err); process.exit(1); });
    """
)


def test_html_setcontent_fails_old_call_and_literal_html_passes(tmp_path) -> None:
    """Rich-editor setContent(string) parses HTML. Commit 660e282a passed the raw line.

    Fixture-observed, not a live Grok DOM. The old call and the generated setter
    run against one HTML innerText model.
    """
    node = (
        shutil.which("node") or "/usr/share/cursor/resources/app/resources/helpers/node"
    )
    assert os.path.isfile(node), (
        "node is required to execute the generated composer setter"
    )
    writer = "interlocutor:agent"
    payloads = {
        "newline": "line1\nline2",
        "markup": "<b>literal</b>",
        "plain": "hello",
        "unicode": "caf\u00e9",
    }
    cases: list[dict[str, object]] = []
    for name, line in payloads.items():
        cases.append(
            {
                "kind": "old_vs_new",
                "id": name,
                "line": line,
                "setter": _set_composer_body(
                    "[data-composer]",
                    line,
                    preserve_newlines=True,
                    draft_writer=writer,
                ),
            }
        )
    cases.extend(
        [
            {
                "kind": "draft",
                "id": "foreign",
                "draft": "human note",
                "setter": _set_composer_body(
                    "[data-composer]",
                    "hello",
                    preserve_newlines=True,
                    draft_writer=writer,
                ),
            },
            {
                "kind": "draft",
                "id": "same_text_no_owner",
                "draft": "hello",
                "setter": _set_composer_body(
                    "[data-composer]",
                    "hello",
                    preserve_newlines=True,
                    draft_writer=writer,
                ),
            },
            {
                "kind": "draft",
                "id": "owned",
                "draft": "hello",
                "activity": {"ourDraftWriter": writer, "ourDraftSig": "hello"},
                "setter": _set_composer_body(
                    "[data-composer]",
                    "hello",
                    preserve_newlines=True,
                    draft_writer=writer,
                ),
            },
            {
                "kind": "draft",
                "id": "owned_changed",
                "draft": "hello edited",
                "activity": {"ourDraftWriter": writer, "ourDraftSig": "hello"},
                "setter": _set_composer_body(
                    "[data-composer]",
                    "hello",
                    preserve_newlines=True,
                    draft_writer=writer,
                ),
            },
            {
                "kind": "click",
                "id": "click_mismatch",
                "draft": "other",
                "clicker": _click_send_body(
                    "[data-composer]", "hello", preserve_newlines=True
                ),
            },
            {
                "kind": "click",
                "id": "click_match",
                "draft": "hello",
                "nonce": "n1",
                "clicker": _click_send_body(
                    "[data-composer]", "hello", preserve_newlines=True, abort_nonce="n1"
                ),
            },
        ]
    )
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    script = tmp_path / "fixture.js"
    script.write_text(_FIXTURE_JS, encoding="utf-8")
    proc = subprocess.run(
        [node, str(script), str(spec)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    rows = {row["id"]: row for row in json.loads(proc.stdout)}
    assert rows["newline"]["oldMatch"] is False
    assert rows["newline"]["newMatch"] is True
    assert rows["newline"]["newOk"] is True
    assert rows["markup"]["oldMatch"] is False
    assert rows["markup"]["newMatch"] is True
    assert rows["plain"]["oldMatch"] is True
    assert rows["plain"]["newMatch"] is True
    assert rows["unicode"]["oldMatch"] is True
    assert rows["unicode"]["newMatch"] is True
    assert rows["foreign"]["reason"] == "composer_not_empty"
    assert rows["foreign"]["unchanged"] is True
    assert rows["foreign"]["calls"] == 0
    assert rows["same_text_no_owner"]["reason"] == "composer_not_empty"
    assert rows["same_text_no_owner"]["calls"] == 0
    assert rows["owned"]["used"] is True
    assert rows["owned"]["calls"] == 0
    assert rows["owned_changed"]["reason"] == "composer_not_empty"
    assert rows["owned_changed"]["unchanged"] is True
    assert rows["click_mismatch"]["reason"] == "composer_text_mismatch"
    assert rows["click_mismatch"]["clicks"] == 0
    assert rows["click_match"]["ok"] is True
    assert rows["click_match"]["clicks"] == 1


@pytest.mark.asyncio
async def test_send_timeout_keeps_clicked_unknown_and_inserted_phase(
    monkeypatch,
) -> None:
    async def fake_set(*_a, **_k):
        return {"ok": True, "inserted": True, "pasted": True, "phase": "inserted"}

    async def fake_click(*_a, **_k):
        return {
            "ok": False,
            "reason": "cdp_eval_timeout",
            "clicked_unknown": True,
            "retryable": False,
            "send_clicked": False,
        }

    monkeypatch.setattr(
        "cursor_bridge.dom_composer_paste.dom_set_composer_text", fake_set
    )
    monkeypatch.setattr("cursor_bridge.dom_composer_paste.dom_click_send", fake_click)
    out = await dom_insert_and_send(
        "ws://fixture",
        selector="s",
        line="hi",
        preserve_newlines=True,
        draft_writer="interlocutor:agent",
    )
    assert out["ok"] is False
    assert out["clicked_unknown"] is True
    assert out["pasted"] is True
    assert out["inserted"] is True
    assert out["phase"] == "send_unknown"
    assert out["retryable"] is False
