"""Node parses/evaluates Orion JS fragments (tab sync, owned draft, composer preflight)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest
from web_chat_relay.chatgpt_work_composer_cdp import chatgpt_work_preflight_expression
from web_chat_relay.orion_wake import (
    _TAB_SYNC_STATE_JS,
    _owned_pointer_draft_eval,
    _tab_sync_state_eval,
)

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _require_node() -> None:
    if shutil.which("node") is None:
        pytest.fail("node is required for Orion JS snippet tests")


def _node_check(script: str) -> None:
    r = subprocess.run(
        ["node", "--check", "-"],
        input=script.encode(),
        capture_output=True,
    )
    if r.returncode != 0:
        raise AssertionError((r.stderr or r.stdout or b"").decode())


def _run_node(js: str, *, env: dict[str, str] | None = None) -> dict:
    r = subprocess.run(
        ["node", "-e", js],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
    )
    assert r.returncode == 0, r.stderr
    line = (r.stdout or "").strip().splitlines()[-1]
    return json.loads(line)


# Minimal browser stubs for structural-empty / owned-draft probes (no jsdom).
_DOM_MIN = """
const Node = { TEXT_NODE: 3, ELEMENT_NODE: 1 };
const NodeFilter = { SHOW_TEXT: 4, FILTER_ACCEPT: 1 };
function createTreeWalker(root) {
  const out = [];
  function walk(n) {
    if (n.nodeType === 3) { out.push(n); return; }
    for (const c of n.childNodes || []) walk(c);
  }
  walk(root);
  let i = 0;
  return { nextNode() { return i < out.length ? out[i++] : null; } };
}
document = {
  visibilityState: 'visible',
  createTreeWalker,
  querySelector(sel) {
    const s = String(sel || '');
    if (globalThis.__ce_roots && globalThis.__ce_roots[0]) {
      if (s.includes('ProseMirror') || s.includes('composer') || s === '#composer') {
        return globalThis.__ce_roots[0];
      }
    }
    return null;
  },
  querySelectorAll(sel) {
    if (sel === '[contenteditable="true"]') return [...globalThis.__ce_roots || []];
    return [];
  },
  getSelection() { return { isCollapsed: true }; },
  get activeElement() { return globalThis.__ce_roots && globalThis.__ce_roots[0]; },
};
window = {};
function refreshWindowGlobals() {
  window.__ulgOrionWakeTabSync = globalThis.__tabSync || { pointerLine: '' };
  window.__ulgOperatorActivity = globalThis.__opAct || {};
}
refreshWindowGlobals();
"""


def test_tab_sync_js_parses_and_prose_mirror_empty() -> None:
    fn = _TAB_SYNC_STATE_JS.strip()
    _node_check("(" + fn + ")")
    setup = _DOM_MIN + """
const br = { nodeType: 1, tagName: 'BR', childNodes: [], children: [] };
const p = { nodeType: 1, tagName: 'P', childNodes: [br], children: [br] };
br.parentNode = p;
const root = {
  nodeType: 1,
  tagName: 'DIV',
  childNodes: [p],
  children: [p],
  closest: () => null,
  querySelectorAll: () => [],
  querySelector: () => null,
  innerText: '',
  value: '',
};
p.parentNode = root;
globalThis.__ce_roots = [root];
"""
    out = _run_node(setup + "refreshWindowGlobals(); console.log(JSON.stringify((" + fn + ")()));")
    assert out["composerEmpty"] is True


def test_tab_sync_spaces_not_empty() -> None:
    fn = _TAB_SYNC_STATE_JS.strip()
    setup = _DOM_MIN + """
const text = { nodeType: 3, textContent: '   ', parentNode: null };
const span = { nodeType: 1, childNodes: [text] };
text.parentNode = span;
const root = {
  nodeType: 1,
  tagName: 'DIV',
  childNodes: [span],
  children: [span],
  closest: () => null,
  querySelectorAll: () => [],
  querySelector: () => null,
  innerText: '',
  value: '',
};
span.parentNode = root;
globalThis.__ce_roots = [root];
"""
    out = _run_node(setup + "refreshWindowGlobals(); console.log(JSON.stringify((" + fn + ")()));")
    assert out["composerEmpty"] is False


def test_tab_sync_foreign_file_chip_marks_non_empty() -> None:
    fn = _TAB_SYNC_STATE_JS.strip()
    setup = _DOM_MIN + """
const chip = {
  nodeType: 1,
  tagName: 'DIV',
  matches: () => false,
  getAttribute: () => null,
  innerText: '',
};
const form = {
  querySelectorAll(s) {
    return s.includes('file-chip') ? [chip] : [];
  },
};
const br = { nodeType: 1, tagName: 'BR' };
const para = { nodeType: 1, tagName: 'P', childNodes: [br], children: [br] };
br.parentNode = para;
const root = {
  nodeType: 1,
  childNodes: [para],
  children: [para],
  parentNode: null,
  getAttribute: (a) => (a === 'contenteditable' ? 'true' : null),
  querySelector: () => null,
  querySelectorAll() { return []; },
  closest(sel) { return sel === 'form' ? form : null; },
};
para.parentNode = root;
globalThis.__ce_roots = [root];
"""
    out = _run_node(setup + "refreshWindowGlobals(); console.log(JSON.stringify((" + fn + ")()));")
    assert out["composerEmpty"] is False


def test_tab_sync_atom_child_marks_non_empty() -> None:
    fn = _TAB_SYNC_STATE_JS.strip()
    setup = _DOM_MIN + """
const atom = { nodeType: 1, tagName: 'ATOM' };
const br = { nodeType: 1, tagName: 'BR' };
const para = { nodeType: 1, tagName: 'P', childNodes: [atom], children: [atom] };
atom.parentNode = para;
const root = {
  nodeType: 1,
  childNodes: [para],
  children: [para],
  parentNode: null,
  getAttribute: (a) => (a === 'contenteditable' ? 'true' : null),
  querySelector(sel) {
    return String(sel).includes('atom') ? atom : null;
  },
  querySelectorAll() { return []; },
  closest() { return null; },
};
para.parentNode = root;
globalThis.__ce_roots = [root];
"""
    out = _run_node(setup + "refreshWindowGlobals(); console.log(JSON.stringify((" + fn + ")()));")
    assert out["composerEmpty"] is False


def test_owned_pointer_draft_requires_sig_match() -> None:
    line = "hello wake"
    wrapped = _owned_pointer_draft_eval(line)
    _node_check(wrapped)
    ok_setup = _DOM_MIN + """
globalThis.__tabSync = { pointerLine: """ + json.dumps(line) + """ };
globalThis.__opAct = { ourDraftSig: """ + json.dumps(line) + """ };
refreshWindowGlobals();
const text = { nodeType: 3, textContent: 'hello wake', parentNode: null };
const root = { nodeType: 1, childNodes: [text], innerText: 'hello wake' };
text.parentNode = root;
globalThis.__ce_roots = [root];
"""
    out = _run_node(
        ok_setup
        + "console.log(JSON.stringify(eval(process.env.ULG_ORION_EXPR)));",
        env={"ULG_ORION_EXPR": wrapped},
    )
    assert out["ok"] is True

    bad_setup = _DOM_MIN + """
globalThis.__tabSync = { pointerLine: """ + json.dumps(line) + """ };
globalThis.__opAct = { ourDraftSig: 'other' };
refreshWindowGlobals();
const root = { nodeType: 1, childNodes: [], innerText: 'hello wake' };
globalThis.__ce_roots = [root];
"""
    out2 = _run_node(
        bad_setup
        + "console.log(JSON.stringify(eval(process.env.ULG_ORION_EXPR)));",
        env={"ULG_ORION_EXPR": wrapped},
    )
    assert out2["ok"] is False


def test_tab_sync_state_eval_wrapped_parses() -> None:
    _node_check(_tab_sync_state_eval("x"))


def test_chatgpt_work_preflight_expression_parses() -> None:
    exp = chatgpt_work_preflight_expression(
        selector="#composer",
        line="hi",
        preserve_newlines=False,
        expected_conversation_id="6aee30ff-802c-83e8-bc37-abf4c0339c36",
    )
    _node_check("(async () => {" + exp.strip() + "})")


def _trailing_break_root(*, inner_text: str = "\n") -> str:
    return f"""
const br = {{ nodeType: 1, tagName: 'BR', childNodes: [], children: [],
  className: 'ProseMirror-trailingBreak',
  getAttribute(a) {{ return a === 'class' ? 'ProseMirror-trailingBreak' : null; }} }};
const para = {{ nodeType: 1, tagName: 'P', childNodes: [br], children: [br],
  getAttribute() {{ return null; }} }};
br.parentNode = para;
const root = {{
  nodeType: 1, tagName: 'DIV', innerText: {json.dumps(inner_text)}, value: '',
  childNodes: [para], children: [para],
  closest: () => null,
  querySelector(sel) {{
    const s = String(sel);
    if (s.includes('atom')) return globalThis.__atom || null;
    if (s.includes('img')) return globalThis.__img || null;
    return null;
  }},
  querySelectorAll() {{ return []; }},
  getAttribute(a) {{ return a === 'contenteditable' ? 'true' : null; }},
}};
para.parentNode = root;
globalThis.__ce_roots = [root];
"""


def test_tab_sync_observed_trailing_break_is_empty() -> None:
    fn = _TAB_SYNC_STATE_JS.strip()
    setup = _DOM_MIN + _trailing_break_root()
    out = _run_node(setup + "refreshWindowGlobals(); console.log(JSON.stringify((" + fn + ")()));")
    assert out["composerEmpty"] is True


@pytest.mark.parametrize(
    "ce_attr",
    ["false", "FALSE", "False", "  false  "],
)
def test_tab_sync_noneditable_descendant_blocks_empty(ce_attr: str) -> None:
    fn = _TAB_SYNC_STATE_JS.strip()
    setup = _DOM_MIN + _trailing_break_root() + f"""
const blocked = {{ nodeType: 1, tagName: 'SPAN', children: [], childNodes: [],
  getAttribute(a) {{ return a === 'contenteditable' ? {json.dumps(ce_attr)} : null; }} }};
globalThis.__ce_roots[0].children[0].childNodes.push(blocked);
globalThis.__ce_roots[0].children[0].children.push(blocked);
blocked.parentNode = globalThis.__ce_roots[0].children[0];
"""
    out = _run_node(setup + "refreshWindowGlobals(); console.log(JSON.stringify((" + fn + ")()));")
    assert out["composerEmpty"] is False


def test_tab_sync_visible_text_blocks_empty() -> None:
    fn = _TAB_SYNC_STATE_JS.strip()
    setup = _DOM_MIN + """
const text = { nodeType: 3, textContent: 'draft', parentNode: null };
const para = { nodeType: 1, tagName: 'P', childNodes: [text], children: [] };
text.parentNode = para;
const root = {
  nodeType: 1, tagName: 'DIV', childNodes: [para], children: [para],
  closest: () => null, querySelector: () => null, querySelectorAll: () => [],
  getAttribute: (a) => (a === 'contenteditable' ? 'true' : null), innerText: 'draft', value: '',
};
para.parentNode = root;
globalThis.__ce_roots = [root];
"""
    out = _run_node(setup + "refreshWindowGlobals(); console.log(JSON.stringify((" + fn + ")()));")
    assert out["composerEmpty"] is False


def test_preflight_observed_trailing_break_is_empty() -> None:
    cid = "6aed2b2e-1b88-83e8-bc37-c8eb6c56b623"
    exp = chatgpt_work_preflight_expression(
        selector="div.ProseMirror",
        line="wake",
        preserve_newlines=False,
        expected_conversation_id=cid,
    )
    setup = _DOM_MIN + _trailing_break_root() + f"""
location = {{ href: 'https://chatgpt.com/c/{cid}' }};
refreshWindowGlobals();
console.log(JSON.stringify({exp.strip()}));
"""
    out = _run_node(setup)
    assert out.get("ok") is True
    assert out.get("state") == "empty"


@pytest.mark.parametrize(
    "ce_attr",
    ["false", "FALSE", "False", "  false  "],
)
def test_preflight_noneditable_descendant_not_empty(ce_attr: str) -> None:
    cid = "6aed2b2e-1b88-83e8-bc37-c8eb6c56b623"
    exp = chatgpt_work_preflight_expression(
        selector="div.ProseMirror",
        line="wake",
        preserve_newlines=False,
        expected_conversation_id=cid,
    )
    setup = _DOM_MIN + _trailing_break_root() + f"""
const blocked = {{ nodeType: 1, tagName: 'SPAN', children: [], childNodes: [],
  getAttribute(a) {{ return a === 'contenteditable' ? {json.dumps(ce_attr)} : null; }} }};
globalThis.__ce_roots[0].children[0].childNodes.push(blocked);
blocked.parentNode = globalThis.__ce_roots[0].children[0];
location = {{ href: 'https://chatgpt.com/c/{cid}' }};
refreshWindowGlobals();
console.log(JSON.stringify({exp.strip()}));
"""
    out = _run_node(setup)
    assert out.get("reason") == "composer_not_empty"
