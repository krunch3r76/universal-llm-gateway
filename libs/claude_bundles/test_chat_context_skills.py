"""Offline tests for Context→Skills section parse (arc 6895 code gate)."""

from __future__ import annotations

import pytest

from claude_bundles.chat_context_disclosure import disclosure_is_expanded
from claude_bundles.chat_context_skills import (
    _ASSISTANT_TURN_BADGE_JS,
    _SKILL_INDUCTION_SNAPSHOT_JS,
    ChatContextSkillsError,
    LoadedSkillsReport,
    parse_loaded_skills_from_turn_badges,
    parse_session_activity_skills,
    parse_skills_from_context_section,
    require_chat_surface,
    scrape_chat_turn_loaded_skills,
    scrape_loaded_skills,
    snapshot_skill_induction_before_submit,
)

pytestmark = pytest.mark.offline


def test_parse_fable_probe_context_section_verbatim() -> None:
    """Live Fable CSE section text (2026-08-07) — only reasoning-posture bound."""
    section = """Progress
See task progress for longer tasks.
Outputs
View and open files created during this task.
Context
Skills
reasoning-posture
"""
    assert parse_skills_from_context_section(section) == ("reasoning-posture",)


def test_parse_both_required_skills() -> None:
    section = """Context
Skills
reasoning-posture
consult-posture
"""
    assert parse_skills_from_context_section(section) == (
        "reasoning-posture",
        "consult-posture",
    )


def test_parse_stops_at_next_section() -> None:
    section = """Context
Skills
reasoning-posture
Files
some-file.md
"""
    assert parse_skills_from_context_section(section) == ("reasoning-posture",)


def test_parse_empty_skills_group() -> None:
    section = """Context
Skills
"""
    assert parse_skills_from_context_section(section) == ()


def test_parse_no_skills_heading() -> None:
    assert parse_skills_from_context_section("Context\nFiles\nfoo") == ()


def test_pre_use_open_context_without_skills_group() -> None:
    """a:38612 — Skills heading absent before first Use/<slug> is expected."""
    report = LoadedSkillsReport(
        url="https://claude.ai/cowork/cse_x",
        skills=(),
        context_found=True,
        skills_heading_found=False,
        model_label=None,
        selectors=(),
        raw_section_text="",
    )
    assert report.pre_use_skills_rail is True
    assert report.skills_rail_post_use is False
    assert parse_skills_from_context_section("Context\n") == ()


def test_open_aria_is_expanded_without_a_second_click() -> None:
    assert disclosure_is_expanded("true", list_visible=False) is True
    assert disclosure_is_expanded("TRUE", list_visible=False) is True


def test_closed_aria_stays_collapsed_even_if_text_remains() -> None:
    assert disclosure_is_expanded("false", list_visible=True) is False


def test_missing_aria_follows_list_visibility() -> None:
    assert disclosure_is_expanded(None, list_visible=True) is True
    assert disclosure_is_expanded("", list_visible=False) is False
    assert disclosure_is_expanded("  ", list_visible=False) is False


def test_missing_required() -> None:
    report = LoadedSkillsReport(
        url="https://claude.ai/cowork/cse_x",
        skills=("reasoning-posture",),
        context_found=True,
        skills_heading_found=True,
        model_label="Fable 5 High",
        selectors=(),
        raw_section_text="Skills\nreasoning-posture",
    )
    assert report.missing(["reasoning-posture", "consult-posture"]) == (
        "consult-posture",
    )


class _UrlPage:
    def __init__(self, url: str) -> None:
        self.url = url


def test_local_cowork_session_is_a_compose_surface() -> None:
    url = "https://claude.ai/cowork/local_0e29ba8a-f756-40d5-949c-39e9b7d85007"
    assert require_chat_surface(_UrlPage(url)) == url


def test_cse_cowork_session_stays_a_compose_surface() -> None:
    url = "https://claude.ai/cowork/cse_01ToWejFedjBWFbKXbAxeKLZ"
    assert require_chat_surface(_UrlPage(url)) == url


def test_settings_url_is_not_a_compose_surface() -> None:
    with pytest.raises(ChatContextSkillsError, match="cowork/local_"):
        require_chat_surface(_UrlPage("https://claude.ai/settings/skills"))


class _DisclosurePage:
    url = "https://claude.ai/cowork/cse_x"

    def __init__(self, *, reads: list[dict], click_result: dict | None = None) -> None:
        self.reads = list(reads)
        self.click_result = click_result or {
            "found": True,
            "clicked": True,
            "aria_expanded": None,
            "list_visible": False,
        }
        self.evaluate_calls: list[dict] = []
        self.timeouts: list[int] = []

    async def evaluate(self, _js: str, spec: dict) -> dict:
        self.evaluate_calls.append(dict(spec))
        if spec.get("click"):
            return dict(self.click_result)
        if not self.reads:
            return {
                "found": False,
                "aria_expanded": None,
                "list_visible": False,
                "clicked": False,
            }
        return dict(self.reads.pop(0))

    async def wait_for_timeout(self, ms: int) -> None:
        self.timeouts.append(ms)


@pytest.mark.asyncio
async def test_expand_context_frame_open_header_does_not_click() -> None:
    from claude_bundles.chat_context_disclosure import expand_context_frame

    open_state = {
        "found": True,
        "aria_expanded": "true",
        "list_visible": False,
        "clicked": False,
    }
    page = _DisclosurePage(reads=[open_state, open_state])
    assert await expand_context_frame(page) is True
    assert all(not c.get("click") for c in page.evaluate_calls)
    assert page.timeouts == []


@pytest.mark.asyncio
async def test_expand_context_frame_collapsed_header_clicks_once() -> None:
    from claude_bundles.chat_context_disclosure import expand_context_frame

    collapsed = {
        "found": True,
        "aria_expanded": "false",
        "list_visible": False,
        "clicked": False,
    }
    opened = {
        "found": True,
        "aria_expanded": "true",
        "list_visible": True,
        "clicked": False,
    }
    page = _DisclosurePage(reads=[collapsed, collapsed, opened])
    assert await expand_context_frame(page) is True
    click_calls = [c for c in page.evaluate_calls if c.get("click")]
    assert len(click_calls) == 1
    assert page.timeouts == [400]


@pytest.mark.asyncio
async def test_expand_context_frame_stays_collapsed_after_one_click() -> None:
    from claude_bundles.chat_context_disclosure import expand_context_frame

    collapsed = {
        "found": True,
        "aria_expanded": "false",
        "list_visible": False,
        "clicked": False,
    }
    page = _DisclosurePage(reads=[collapsed, collapsed, collapsed])
    with pytest.raises(ChatContextSkillsError, match="still collapsed"):
        await expand_context_frame(page)
    click_calls = [c for c in page.evaluate_calls if c.get("click")]
    assert len(click_calls) == 1


def test_parse_session_activity_skills_aria_label_single_and_multi() -> None:
    assert parse_session_activity_skills("Skills: reasoning-posture") == (
        "reasoning-posture",
    )
    assert parse_session_activity_skills(
        "Skills: reasoning-posture, hypothesize-simulate"
    ) == ("reasoning-posture", "hypothesize-simulate")
    assert parse_session_activity_skills(
        "Skills: reasoning-posture and hypothesize-simulate"
    ) == ("reasoning-posture", "hypothesize-simulate")


def test_parse_session_activity_skills_truncate_fallback() -> None:
    assert parse_session_activity_skills("reasoning-posture") == ("reasoning-posture",)
    assert parse_session_activity_skills("reasoning-posture, hypothesize-simulate") == (
        "reasoning-posture",
        "hypothesize-simulate",
    )


def test_parse_loaded_skills_from_turn_badges_single_and_multi() -> None:
    single = "Loaded the reasoning-posture skill."
    multi = "Loaded the reasoning-posture and hypothesize-simulate skills"
    list_form = "Loaded the reasoning-posture, hypothesize-simulate skills."
    used_bare = "Used ulg-code"
    used_named = "Used the reasoning-posture and hypothesize-simulate skills."
    assert parse_loaded_skills_from_turn_badges(single) == ("reasoning-posture",)
    assert parse_loaded_skills_from_turn_badges(multi) == (
        "reasoning-posture",
        "hypothesize-simulate",
    )
    assert parse_loaded_skills_from_turn_badges(list_form) == (
        "reasoning-posture",
        "hypothesize-simulate",
    )
    assert parse_loaded_skills_from_turn_badges(used_bare) == ("ulg-code",)
    assert parse_loaded_skills_from_turn_badges(used_named) == (
        "reasoning-posture",
        "hypothesize-simulate",
    )
    assert parse_loaded_skills_from_turn_badges("Used toys integration") == ()
    assert parse_loaded_skills_from_turn_badges("reasoning-posture") == ()


class _BadgeTurnPage:
    url = "https://claude.ai/chat/c1bb5193-9607-4b8b-81ac-5d4aeea0867c"

    def __init__(
        self,
        *,
        captions: list[dict[str, str]] | None = None,
        session_labels: list[str] | None = None,
    ) -> None:
        self._payload = {
            "session_labels": session_labels or [],
            "captions": captions or [],
        }

    async def evaluate(self, js: str) -> dict[str, object]:
        if "Session activity panel" in js or "session_labels" in js:
            return dict(self._payload)
        return {}

    async def wait_for_timeout(self, _ms: int) -> None:
        return None


_NODE_DOM_HARNESS_JS = r"""
const fs = require('fs');
const vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));

function parseSimple(token) {
  const attrRe = /\[([a-zA-Z0-9_-]+)(?:([*$]?=)"([^"]*)")?\]/g;
  const tagMatch = token.match(/^[a-zA-Z0-9_-]+/);
  const tag = tagMatch ? tagMatch[0].toLowerCase() : null;
  const attrs = [];
  let m;
  while ((m = attrRe.exec(token)) !== null) {
    attrs.push({ name: m[1], op: m[2] || null, val: m[3] || '' });
  }
  return { tag, attrs };
}

function matchSimple(el, simple) {
  if (!el || el.nodeType !== 1) return false;
  if (simple.tag && el.tag !== simple.tag) return false;
  for (const a of simple.attrs) {
    const has = Object.prototype.hasOwnProperty.call(el.attrs, a.name);
    if (!a.op) {
      if (!has) return false;
      continue;
    }
    if (!has) return false;
    const v = String(el.attrs[a.name] || '');
    if (a.op === '=' && v !== a.val) return false;
    if (a.op === '*=' && !v.includes(a.val)) return false;
    if (a.op === '$=' && !v.endsWith(a.val)) return false;
  }
  return true;
}

function splitTopLevelCommas(sel) {
  const out = [];
  let depth = 0;
  let cur = '';
  for (const ch of sel) {
    if (ch === '[') depth += 1;
    if (ch === ']') depth -= 1;
    if (ch === ',' && depth === 0) {
      if (cur.trim()) out.push(cur.trim());
      cur = '';
    } else {
      cur += ch;
    }
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

function splitSelectorParts(sel) {
  const out = [];
  let depth = 0;
  let inQuote = false;
  let cur = '';
  for (const ch of sel) {
    if (ch === '"') inQuote = !inQuote;
    if (!inQuote && ch === '[') depth += 1;
    if (!inQuote && ch === ']') depth -= 1;
    if (!inQuote && depth === 0 && /\s/.test(ch)) {
      if (cur.trim()) out.push(cur.trim());
      cur = '';
    } else {
      cur += ch;
    }
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

function matchSingleSelector(el, sel, scopeEl) {
  let s = sel.trim();
  let directFromScope = false;
  if (s.startsWith(':scope')) {
    s = s.slice(':scope'.length).trim();
    if (s.startsWith('>')) {
      directFromScope = true;
      s = s.slice(1).trim();
    }
  }
  const parts = splitSelectorParts(s).map(parseSimple);
  if (!parts.length) return false;
  if (!matchSimple(el, parts[parts.length - 1])) return false;
  if (parts.length === 1) {
    return directFromScope ? el.parent === scopeEl : true;
  }
  let cur = el.parent;
  for (let i = parts.length - 2; i >= 0; i--) {
    if (i === 0 && directFromScope) {
      while (cur && !(matchSimple(cur, parts[0]) && cur.parent === scopeEl)) {
        cur = cur.parent;
      }
      return Boolean(cur);
    }
    while (cur && !matchSimple(cur, parts[i])) {
      cur = cur.parent;
    }
    if (!cur) return false;
    cur = cur.parent;
  }
  return true;
}

function makeNode(spec, parent) {
  if (typeof spec === 'string') {
    return { nodeType: 3, text: spec, parent };
  }
  const node = {
    nodeType: 1,
    tag: String(spec.tag || 'div').toLowerCase(),
    attrs: Object.assign({}, spec.attrs || {}),
    parent: parent || null,
    children: [],
    nextElementSibling: null,
    get parentElement() { return this.parent; },
    get textContent() {
      return this.children.map((c) => (c.nodeType === 3 ? c.text : c.textContent)).join('');
    },
    get innerText() {
      return this.children
        .map((c) => (c.nodeType === 3 ? c.text : c.innerText))
        .join('\n');
    },
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attrs, name)
        ? String(this.attrs[name])
        : null;
    },
    matches(selector) {
      return splitTopLevelCommas(selector).some((s) =>
        matchSingleSelector(this, s, null)
      );
    },
    closest(selector) {
      let cur = this;
      while (cur && cur.nodeType === 1) {
        if (cur.matches(selector)) return cur;
        cur = cur.parent;
      }
      return null;
    },
    querySelectorAll(selector) {
      const branches = splitTopLevelCommas(selector);
      const found = [];
      const walk = (n) => {
        for (const ch of n.children) {
          if (ch.nodeType === 1) {
            if (branches.some((b) => matchSingleSelector(ch, b, this))) {
              found.push(ch);
            }
            walk(ch);
          }
        }
      };
      walk(this);
      return found;
    },
    querySelector(selector) {
      const all = this.querySelectorAll(selector);
      return all.length ? all[0] : null;
    },
  };
  node.children = (spec.children || []).map((c) => makeNode(c, node));
  const elemChildren = node.children.filter((c) => c.nodeType === 1);
  for (let i = 0; i < elemChildren.length - 1; i++) {
    elemChildren[i].nextElementSibling = elemChildren[i + 1];
  }
  return node;
}

const win = {};
const ctx = vm.createContext({ window: win, document: null });
win.window = win;
const out = [];
let rootTree = null;
for (const step of input.steps) {
  if (step.set_tree) {
    rootTree = makeNode(step.set_tree, null);
    ctx.document = rootTree;
    win.document = rootTree;
  }
  if (step.append_to_body && rootTree) {
    const added = makeNode(step.append_to_body, rootTree);
    const prevElems = rootTree.children.filter((c) => c.nodeType === 1);
    if (prevElems.length) prevElems[prevElems.length - 1].nextElementSibling = added;
    rootTree.children.push(added);
  }
  if (step.set_panel_label && rootTree) {
    const row = rootTree.querySelector('[data-kind-row="skills"]');
    if (row) row.attrs['aria-label'] = step.set_panel_label;
  }
  if (step.eval_js) {
    const fn = vm.runInContext(`(${step.eval_js})`, ctx);
    out.push(fn());
  }
}
process.stdout.write(JSON.stringify(out));
"""


def _run_dom_steps_in_node(steps: list[dict[str, object]]) -> list[dict[str, object]]:
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    assert node is not None, "node runtime required to execute DOM JS snippets"
    proc = subprocess.run(
        [node, "-e", _NODE_DOM_HARNESS_JS],
        input=json.dumps({"steps": steps}),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(proc.stdout)


class _EvaluatedDomPage:
    url = "https://claude.ai/chat/c1bb5193-9607-4b8b-81ac-5d4aeea0867c"

    def __init__(self, tree: dict[str, object]) -> None:
        self._steps: list[dict[str, object]] = [{"set_tree": tree}]

    def append_turn(self, node_spec: dict[str, object]) -> None:
        self._steps.append({"append_to_body": node_spec})

    def set_panel_label(self, aria_label: str) -> None:
        self._steps.append({"set_panel_label": aria_label})

    async def evaluate(self, js: str) -> dict[str, object]:
        self._steps.append({"eval_js": js})
        results = _run_dom_steps_in_node(self._steps)
        return results[-1]

    async def wait_for_timeout(self, _ms: int) -> None:
        return None


@pytest.mark.asyncio
async def test_scrape_chat_turn_loaded_skills_reads_session_activity_aria_label() -> (
    None
):
    page = _BadgeTurnPage(session_labels=["Skills: reasoning-posture"])
    assert await scrape_chat_turn_loaded_skills(page) == ("reasoning-posture",)


@pytest.mark.asyncio
async def test_scrape_chat_turn_loaded_skills_prefers_panel_then_caption() -> None:
    page = _BadgeTurnPage(
        session_labels=[],
        captions=[
            {
                "node_id": "item:t1",
                "text": "Loaded the hypothesize-simulate skill.",
            }
        ],
    )
    assert await scrape_chat_turn_loaded_skills(page) == ("hypothesize-simulate",)


@pytest.mark.asyncio
async def test_scrape_chat_turn_loaded_skills_panel_wins_over_caption_order() -> None:
    page = _BadgeTurnPage(
        session_labels=["Skills: reasoning-posture"],
        captions=[
            {
                "node_id": "item:t1",
                "text": "Loaded the hypothesize-simulate skill.",
            }
        ],
    )
    assert await scrape_chat_turn_loaded_skills(page) == (
        "reasoning-posture",
        "hypothesize-simulate",
    )


@pytest.mark.asyncio
async def test_scrape_chat_turn_loaded_skills_empty_fails_closed() -> None:
    page = _BadgeTurnPage(captions=[{"node_id": "item:t1", "text": "Loaded skill"}])
    assert await scrape_chat_turn_loaded_skills(page) == ()


@pytest.mark.asyncio
async def test_scrape_chat_turn_loaded_skills_does_not_trust_user_skills_line() -> None:
    """User-turn ``Skills: <slug>`` prose must not count as a load receipt."""
    page = _BadgeTurnPage(
        captions=[{"node_id": "item:t1", "text": "Skills: reasoning-posture"}]
    )
    assert await scrape_chat_turn_loaded_skills(page) == ()


def test_assistant_turn_badge_js_never_reads_innertext_or_step_subjects() -> None:
    assert "innerText" not in _ASSISTANT_TURN_BADGE_JS
    assert "innerText" not in _SKILL_INDUCTION_SNAPSHOT_JS
    assert "cds_subjects" not in _ASSISTANT_TURN_BADGE_JS
    assert "data-cds-row-subject" not in _ASSISTANT_TURN_BADGE_JS
    assert "data-cds-row-title" not in _ASSISTANT_TURN_BADGE_JS


@pytest.mark.asyncio
async def test_forged_prose_line_in_assistant_reply_refuses(monkeypatch) -> None:
    """R3 F1b: model prose ``Loaded the reasoning-posture skill.`` must refuse."""

    async def refuse_expand(_page) -> bool:
        raise ChatContextSkillsError(
            "Context list is not on the rail \u2014 scrape refused until it is expanded"
        )

    monkeypatch.setattr(
        "claude_bundles.chat_context_skills.expand_context_frame", refuse_expand
    )
    dom_page = _EvaluatedDomPage(
        {
            "tag": "body",
            "children": [
                {
                    "tag": "div",
                    "attrs": {
                        "data-testid": "assistant-message",
                        "class": "font-claude-response",
                    },
                    "children": [
                        {
                            "tag": "div",
                            "attrs": {
                                "data-cds": "TurnStatus",
                                "data-item-key": "turn1:grp:th0",
                            },
                            "children": [
                                {
                                    "tag": "span",
                                    "attrs": {"data-morph-key": "done|Thinking"},
                                    "children": ["Thought for 2s"],
                                }
                            ],
                        },
                        {
                            "tag": "p",
                            "children": ["Loaded the reasoning-posture skill."],
                        },
                        {
                            "tag": "p",
                            "children": ["Used reasoning-posture"],
                        },
                    ],
                }
            ],
        }
    )
    assert await scrape_chat_turn_loaded_skills(dom_page) == ()
    with pytest.raises(ChatContextSkillsError, match="not on the rail"):
        await scrape_loaded_skills(dom_page)


@pytest.mark.asyncio
async def test_slug_shaped_turn_status_step_subject_alone_refuses(monkeypatch) -> None:
    """R3 F1a: a slug-shaped ``TurnStatusStep`` subject/title alone must refuse."""

    async def refuse_expand(_page) -> bool:
        raise ChatContextSkillsError(
            "Context list is not on the rail \u2014 scrape refused until it is expanded"
        )

    monkeypatch.setattr(
        "claude_bundles.chat_context_skills.expand_context_frame", refuse_expand
    )
    dom_page = _EvaluatedDomPage(
        {
            "tag": "body",
            "children": [
                {
                    "tag": "div",
                    "attrs": {"data-testid": "assistant-message"},
                    "children": [
                        {
                            "tag": "div",
                            "attrs": {
                                "data-cds": "TurnStatus",
                                "data-item-key": "turn1:grp:th0",
                            },
                            "children": [
                                {
                                    "tag": "div",
                                    "attrs": {"data-cds-row": ""},
                                    "children": [
                                        {
                                            "tag": "span",
                                            "attrs": {
                                                "data-morph-key": "done|Ran a command"
                                            },
                                            "children": ["Ran a command"],
                                        }
                                    ],
                                },
                                {
                                    "tag": "div",
                                    "attrs": {"data-cds-row-panel": ""},
                                    "children": [
                                        {
                                            "tag": "div",
                                            "attrs": {"data-cds": "TurnStatusStep"},
                                            "children": [
                                                {
                                                    "tag": "div",
                                                    "attrs": {
                                                        "data-cds-row-title": (
                                                            "reasoning-posture"
                                                        )
                                                    },
                                                    "children": [
                                                        {
                                                            "tag": "span",
                                                            "attrs": {
                                                                "data-morph-key": (
                                                                    "done|Loaded skill"
                                                                )
                                                            },
                                                            "children": [
                                                                "Loaded skill"
                                                            ],
                                                        },
                                                        {
                                                            "tag": "span",
                                                            "attrs": {
                                                                "data-cds-row-subject": (
                                                                    ""
                                                                )
                                                            },
                                                            "children": [
                                                                "reasoning-posture"
                                                            ],
                                                        },
                                                    ],
                                                }
                                            ],
                                        }
                                    ],
                                },
                            ],
                        }
                    ],
                }
            ],
        }
    )
    assert await scrape_chat_turn_loaded_skills(dom_page) == ()
    with pytest.raises(ChatContextSkillsError, match="not on the rail"):
        await scrape_loaded_skills(dom_page)


@pytest.mark.asyncio
async def test_used_slug_caption_passes_in_dom(monkeypatch) -> None:
    """R3 (1): ``Used <slug>`` TurnStatus caption (run e681ec54) passes."""

    async def refuse_expand(_page) -> bool:
        raise ChatContextSkillsError(
            "Context list is not on the rail \u2014 scrape refused until it is expanded"
        )

    monkeypatch.setattr(
        "claude_bundles.chat_context_skills.expand_context_frame", refuse_expand
    )
    dom_page = _EvaluatedDomPage(
        {
            "tag": "body",
            "children": [
                {
                    "tag": "div",
                    "attrs": {"data-testid": "assistant-message"},
                    "children": [
                        {
                            "tag": "div",
                            "attrs": {
                                "data-cds": "TurnStatus",
                                "data-item-key": "e681ec54:grp:th0",
                            },
                            "children": [
                                {
                                    "tag": "span",
                                    "attrs": {"data-morph-key": "done|Used ulg-code"},
                                    "children": ["Used ulg-code"],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )
    report = await scrape_loaded_skills(dom_page)
    assert report.skills == ("ulg-code",)
    assert report.context_found is False


@pytest.mark.asyncio
async def test_warm_followup_stale_panel_and_caption_refuse_then_new_caption_passes(
    monkeypatch,
) -> None:
    """R3 F2: stale turn-1 panel+caption refuse; new post-submit caption passes."""

    async def refuse_expand(_page) -> bool:
        raise ChatContextSkillsError(
            "Context list is not on the rail \u2014 scrape refused until it is expanded"
        )

    monkeypatch.setattr(
        "claude_bundles.chat_context_skills.expand_context_frame", refuse_expand
    )
    dom_page = _EvaluatedDomPage(
        {
            "tag": "body",
            "children": [
                {
                    "tag": "aside",
                    "attrs": {"aria-label": "Session activity panel"},
                    "children": [
                        {
                            "tag": "div",
                            "attrs": {
                                "data-kind-row": "skills",
                                "aria-label": "Skills: reasoning-posture",
                            },
                        }
                    ],
                },
                {
                    "tag": "div",
                    "attrs": {"data-testid": "assistant-message"},
                    "children": [
                        {
                            "tag": "div",
                            "attrs": {"data-cds": "TurnStatus"},
                            "children": [
                                {
                                    "tag": "span",
                                    "attrs": {
                                        "data-morph-key": (
                                            "done|Loaded the reasoning-posture skill"
                                        )
                                    },
                                    "children": ["Loaded the reasoning-posture skill"],
                                }
                            ],
                        }
                    ],
                },
            ],
        }
    )
    # Snapshot taken BEFORE turn-2 submit captures turn-1 panel + WeakMap node id.
    snap = await snapshot_skill_induction_before_submit(dom_page)
    assert snap.panel_slugs == frozenset({"reasoning-posture"})
    assert len(snap.caption_node_ids) == 1

    # Turn 2 has not loaded anything yet (even if forged prose appears) -> refuses.
    dom_page.append_turn(
        {
            "tag": "div",
            "attrs": {"data-testid": "assistant-message"},
            "children": [
                {"tag": "p", "children": ["Loaded the reasoning-posture skill."]}
            ],
        }
    )
    assert await scrape_chat_turn_loaded_skills(dom_page, snapshot=snap) == ()
    with pytest.raises(ChatContextSkillsError, match="not on the rail"):
        await scrape_loaded_skills(dom_page, snapshot=snap)

    # Turn 2 now mounts a NEW TurnStatus caption node -> passes!
    dom_page.append_turn(
        {
            "tag": "div",
            "attrs": {"data-testid": "assistant-message"},
            "children": [
                {
                    "tag": "div",
                    "attrs": {"data-cds": "TurnStatus"},
                    "children": [
                        {
                            "tag": "span",
                            "attrs": {
                                "data-morph-key": (
                                    "done|Loaded the reasoning-posture skill"
                                )
                            },
                            "children": ["Loaded the reasoning-posture skill"],
                        }
                    ],
                }
            ],
        }
    )
    report = await scrape_loaded_skills(dom_page, snapshot=snap)
    assert report.skills == ("reasoning-posture",)


@pytest.mark.asyncio
async def test_scrape_chat_turn_loaded_skills_ignores_user_and_composer_nodes() -> None:
    class _EvalPage:
        url = "https://claude.ai/chat/x"

        async def evaluate(self, js: str) -> str:
            assert "user-message" in js
            return ""

    assert await scrape_chat_turn_loaded_skills(_EvalPage()) == ()


@pytest.mark.asyncio
async def test_scrape_loaded_skills_falls_back_to_assistant_badge_when_rail_absent(
    monkeypatch,
) -> None:
    async def refuse_expand(_page) -> bool:
        raise ChatContextSkillsError(
            "Context list is not on the rail \u2014 scrape refused until it is expanded"
        )

    async def turn_badges(_page) -> tuple[str, ...]:
        return ("reasoning-posture", "hypothesize-simulate")

    monkeypatch.setattr(
        "claude_bundles.chat_context_skills.expand_context_frame", refuse_expand
    )
    monkeypatch.setattr(
        "claude_bundles.chat_context_skills.scrape_chat_turn_loaded_skills",
        turn_badges,
    )
    page = _BadgeTurnPage()
    report = await scrape_loaded_skills(page)
    assert report.skills == ("reasoning-posture", "hypothesize-simulate")
    assert report.context_found is False
    assert report.skills_rail_post_use is True
    assert any("Session activity panel" in s for s in report.selectors)


@pytest.mark.asyncio
async def test_collapsed_rail_does_not_fall_back_to_badges(monkeypatch) -> None:
    async def collapsed(_page) -> bool:
        raise ChatContextSkillsError(
            "Context list is still collapsed after open — scrape refused"
        )

    async def should_not_run(_page) -> tuple[str, ...]:
        raise AssertionError("badge fallback must not run on collapsed rail")

    monkeypatch.setattr(
        "claude_bundles.chat_context_skills.expand_context_frame", collapsed
    )
    monkeypatch.setattr(
        "claude_bundles.chat_context_skills.scrape_chat_turn_loaded_skills",
        should_not_run,
    )
    page = _BadgeTurnPage(
        captions=[
            {
                "node_id": "item:t1",
                "text": "Loaded the reasoning-posture skill.",
            }
        ]
    )
    with pytest.raises(ChatContextSkillsError, match="still collapsed"):
        await scrape_loaded_skills(page)


@pytest.mark.asyncio
async def test_scrape_loaded_skills_session_activity_live_path(monkeypatch) -> None:
    async def refuse_expand(_page) -> bool:
        raise ChatContextSkillsError(
            "Context list is not on the rail \u2014 scrape refused until it is expanded"
        )

    monkeypatch.setattr(
        "claude_bundles.chat_context_skills.expand_context_frame", refuse_expand
    )
    page = _BadgeTurnPage(
        session_labels=["Skills: reasoning-posture, hypothesize-simulate"]
    )
    report = await scrape_loaded_skills(page)
    assert report.skills == ("reasoning-posture", "hypothesize-simulate")
    assert report.context_found is False


@pytest.mark.asyncio
async def test_scrape_loaded_skills_fails_closed_when_no_skill_signal(
    monkeypatch,
) -> None:
    async def refuse_expand(_page) -> bool:
        raise ChatContextSkillsError(
            "Context list is not on the rail \u2014 scrape refused until it is expanded"
        )

    monkeypatch.setattr(
        "claude_bundles.chat_context_skills.expand_context_frame", refuse_expand
    )
    page = _BadgeTurnPage(captions=[{"node_id": "item:t1", "text": "Loaded skill"}])
    with pytest.raises(ChatContextSkillsError, match="not on the rail"):
        await scrape_loaded_skills(page)
