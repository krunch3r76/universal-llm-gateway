"""Execute the embedded ulgComposerDomEmpty predicate in Node."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from cursor_bridge.dom_composer_paste import _ULG_NORM_COMPOSER_JS

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node not installed",
)


def _eval(source: str, fixture: dict) -> bool:
    script = f"""
{source}
const spec = {json.dumps(fixture)};
function el(tag, props, kids) {{
  const node = {{
    tagName: tag,
    className: props.className || '',
    attrs: props.attrs || {{}},
    children: kids || [],
    editor: props.editor,
    get innerText() {{ return props.innerText || ''; }},
    get innerHTML() {{ return props.innerHTML || ''; }},
    getAttribute(name) {{ return this.attrs[name] || null; }},
    querySelector(sel) {{
      const all = [];
      const walk = (n) => {{ n.children.forEach(c => {{ all.push(c); walk(c); }}); }};
      walk(this);
      return all.find(n => {{
        if (sel.includes('img') && n.tagName === 'IMG') return true;
        if (sel.includes('atom') && n.tagName === 'ATOM') return true;
        if (sel.includes('svg') && n.tagName === 'SVG') return true;
        if (sel.includes('a[href]') && n.tagName === 'A' && n.getAttribute('href')) return true;
        const id = n.getAttribute('data-testid') || '';
        const cls = String(n.className || '');
        if (sel.includes('attachment') && (id.includes('attachment') || cls.includes('attachment'))) return true;
        if (sel.includes('file-chip') && id.includes('file-chip')) return true;
        if (sel.includes('chip') && cls.includes('chip')) return true;
        return false;
      }}) || null;
    }},
  }};
  return node;
}}
const root = el(spec.tag || 'DIV', spec, (spec.children || []).map(ch => build(ch)));
function build(spec) {{
  return el(spec.tag, spec, (spec.children || []).map(build));
}}
process.stdout.write(JSON.stringify(ulgComposerDomEmpty(spec.null_el ? null : root)));
"""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


OBSERVED_EMPTY = {
    "innerText": "\n",
    "innerHTML": '<p><br class="ProseMirror-trailingBreak"></p>',
    "children": [
        {
            "tag": "P",
            "children": [
                {
                    "tag": "BR",
                    "className": "ProseMirror-trailingBreak",
                    "attrs": {"class": "ProseMirror-trailingBreak"},
                }
            ],
        }
    ],
}


def test_observed_trailing_break_without_editor_is_empty() -> None:
    assert _eval(_ULG_NORM_COMPOSER_JS, OBSERVED_EMPTY) is True


@pytest.mark.parametrize(
    "fixture",
    [
        {
            "innerText": "  ",
            "innerHTML": "<p>  </p>",
            "children": [{"tag": "P", "innerText": "  "}],
        },
        {
            "innerText": "hello",
            "innerHTML": "<p>hello</p>",
            "children": [{"tag": "P", "innerText": "hello"}],
        },
        {"innerText": "\\n", "innerHTML": "<p>\\n</p>", "children": [{"tag": "P"}]},
        {
            "innerText": "\n",
            "innerHTML": '<p><br class="ProseMirror-trailingBreak"></p><img>',
            "children": [
                {
                    "tag": "P",
                    "children": [
                        {
                            "tag": "BR",
                            "className": "ProseMirror-trailingBreak",
                            "attrs": {"class": "ProseMirror-trailingBreak"},
                        }
                    ],
                },
                {"tag": "IMG"},
            ],
        },
        {
            "innerText": "\n",
            "innerHTML": '<p><br class="ProseMirror-trailingBreak"></p>',
            "children": [
                {
                    "tag": "P",
                    "children": [
                        {
                            "tag": "BR",
                            "className": "ProseMirror-trailingBreak",
                            "attrs": {"class": "ProseMirror-trailingBreak"},
                        },
                        {"tag": "ATOM"},
                    ],
                }
            ],
        },
        {
            "innerText": "\n",
            "innerHTML": '<p><br class="ProseMirror-trailingBreak"></p><span data-testid="file-chip"></span>',
            "children": [
                {
                    "tag": "P",
                    "children": [
                        {
                            "tag": "BR",
                            "className": "ProseMirror-trailingBreak",
                            "attrs": {"class": "ProseMirror-trailingBreak"},
                        }
                    ],
                },
                {
                    "tag": "SPAN",
                    "attrs": {"data-testid": "file-chip"},
                    "className": "chip",
                },
            ],
        },
        {
            "innerText": "\n",
            "innerHTML": '<p><br class="ProseMirror-trailingBreak"></p><a href="https://example.test"></a>',
            "children": [
                {
                    "tag": "P",
                    "children": [
                        {
                            "tag": "BR",
                            "className": "ProseMirror-trailingBreak",
                            "attrs": {"class": "ProseMirror-trailingBreak"},
                        }
                    ],
                },
                {"tag": "A", "attrs": {"href": "https://example.test"}},
            ],
        },
    ],
)
def test_text_and_nontext_drafts_stay_nonempty(fixture: dict) -> None:
    assert _eval(_ULG_NORM_COMPOSER_JS, fixture) is False


def test_known_empty_placeholders_stay_empty() -> None:
    assert _eval(_ULG_NORM_COMPOSER_JS, {"null_el": True}) is True
    assert _eval(_ULG_NORM_COMPOSER_JS, {"innerText": "", "innerHTML": ""}) is True
    marked = {
        "innerText": "\n",
        "innerHTML": '<p class="is-editor-empty"><br class="ProseMirror-trailingBreak"></p>',
        "children": [
            {
                "tag": "P",
                "children": [
                    {
                        "tag": "BR",
                        "className": "ProseMirror-trailingBreak",
                        "attrs": {"class": "ProseMirror-trailingBreak"},
                    }
                ],
            }
        ],
    }
    assert _eval(_ULG_NORM_COMPOSER_JS, marked) is True
