"""Scripted scrape of claude.ai chat UI Context → Skills (non-LLM).

Operator correction (arc 6895): a model's ``SKILLS_PROBE_OK`` self-report is not
evidence that Customize skills are session-loaded. The chat right-rail
**Context** frame lists the skills the product actually bound. This module
reads that DOM — deterministic, zero tokens. The Skills list is scraped only
after its disclosure is confirmed open; a collapsed rail is expanded once,
and a still-closed list raises instead of returning no slugs.

Operator correction (a:38612): the **Skills** group under Context is populated
**after** Claude uses a skill via ``Use the … skill`` or ``/<slug>`` — not at
session open. ``context_found=true`` with ``skills_heading_found=false`` and
``skills=()`` on a fresh Cowork turn is **pre-use**, not a missing heading.

Loci (live 2026-08-07, Cowork CSE ``cse_01AhPKZ5C8gb1py1m3xHbEeL``):

- Context toggle: ``[data-section-header][role="button"]`` labeled ``Context``
- Skills rows live in that section's open grid (``header.nextElementSibling``)
- Kebab-case slugs appear under a ``Skills`` label once the model has used them
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Page

from claude_bundles.chat_context_disclosure import expand_context_frame
from claude_bundles.skills_ui_panel import DEFAULT_CDP_URL, connect_cdp

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+$")
_NAMED_SKILL_CAPTION_RE = re.compile(
    r"^(?:loaded|used)\s+the\s+(.+?)\s+skills?\.?$",
    re.I,
)
_USED_BARE_SKILL_CAPTION_RE = re.compile(
    r"^used\s+(?!the\s)(.+?)(?:\s+skills?)?\.?$",
    re.I,
)
_LOADED_SKILL_LIST_SPLIT = re.compile(r"\s*,\s*and\s+|\s*,\s*|\s+and\s+", re.I)
_COMPOUND_CAPTION_SPLIT = re.compile(
    r",\s+(?=(?:loaded|used|searched|ran)\s+)",
    re.I,
)
_BADGE_SELECTOR = (
    '[data-cds="TurnStatus"] caption: '
    "Loaded the <slug> skill(s) | Used [the] <slug> [skill(s)]"
)
_SESSION_ACTIVITY_SELECTOR = (
    'aside[aria-label="Session activity panel"] [data-kind-row="skills"][aria-label]'
)
_SKILLS_PANEL_PREFIX = re.compile(r"^Skills:\s*", re.I)
# ``/cowork/local_<uuid>`` is the session Cowork opens after the induction
# submit (thread 12831). ``cse_`` is the older session id. Both are compose.
_COMPOSE_URL = re.compile(
    r"/new(?:\?|$|#)|/cowork/(?:cse_|local_)|/chat/|/code(?:/|$|\?|#)",
    re.I,
)

# Right-rail section labels — stop collecting when another section begins.
_SECTION_STOP = re.compile(
    r"^(Progress|Outputs|Context|Files|Connectors|Memory|Artifacts)$", re.I
)


class ChatContextSkillsError(RuntimeError):
    """Scrape refused because the Context rail or its Skills list could not be read.

    Callers treat this as fail-closed: a collapsed list is not an empty skill
    set. ``record_post_submit_skills_receipt`` catches it and records the error
    without aborting a submit that already happened.
    """


@dataclass(frozen=True)
class LoadedSkillsReport:
    """Observation from the chat Context → Skills frame."""

    url: str
    skills: tuple[str, ...]
    context_found: bool
    skills_heading_found: bool
    model_label: str | None
    selectors: tuple[str, ...]
    raw_section_text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def missing(self, required: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        have = {s.lower() for s in self.skills}
        return tuple(s for s in required if s.lower() not in have)

    @property
    def ok_nonempty(self) -> bool:
        return self.context_found and bool(self.skills)

    @property
    def skills_rail_post_use(self) -> bool:
        """True once the open Context grid shows a Skills group or bound slugs."""
        return bool(self.skills) or self.skills_heading_found

    @property
    def pre_use_skills_rail(self) -> bool:
        """Open Context with no Skills group yet — normal before first Use/<slug>."""
        return self.context_found and not self.skills_rail_post_use


_SELECTORS_USED = (
    '[data-section-header][role="button"]: Context',
    "Context panel: header.nextElementSibling innerText",
    "under Context: exact heading Skills (post-use)",
    "skill rows: kebab-case slug text under Skills group",
)


def _ordered_unique_slugs(slugs: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for slug in slugs:
        if slug in seen:
            continue
        seen.add(slug)
        out.append(slug)
    return tuple(out)


def parse_session_activity_skills(raw: str) -> tuple[str, ...]:
    """Parse kebab slugs from Session activity skills row text or aria-label."""
    text = raw.strip()
    if not text:
        return ()
    text = _SKILLS_PANEL_PREFIX.sub("", text).strip()
    out: list[str] = []
    seen: set[str] = set()
    for part in _LOADED_SKILL_LIST_SPLIT.split(text):
        slug = part.strip().lower()
        if not _SLUG_RE.fullmatch(slug) or slug in seen:
            continue
        seen.add(slug)
        out.append(slug)
    return tuple(out)


@dataclass(frozen=True)
class SkillInductionSnapshot:
    """Pre-submit baseline of session-panel slugs and ``TurnStatus`` node ids.

    Captured immediately before submit so warm follow-ups reject stale Turn-1
    panel entries and stale Turn-1 ``TurnStatus`` captions while accepting new
    panel slugs or captions on newly mounted ``TurnStatus`` nodes.
    """

    panel_slugs: frozenset[str] = frozenset()
    caption_node_ids: frozenset[str] = frozenset()


def _parse_caption_clause(clause: str) -> list[str]:
    cleaned = clause.strip()
    if not cleaned:
        return []
    named = _NAMED_SKILL_CAPTION_RE.match(cleaned)
    if named:
        parts = [
            part.strip().lower()
            for part in _LOADED_SKILL_LIST_SPLIT.split(named.group(1))
        ]
        return [part for part in parts if _SLUG_RE.fullmatch(part)]
    used = _USED_BARE_SKILL_CAPTION_RE.match(cleaned)
    if used:
        parts = [
            part.strip().lower()
            for part in _LOADED_SKILL_LIST_SPLIT.split(used.group(1))
        ]
        if parts and all(_SLUG_RE.fullmatch(part) for part in parts):
            return parts
    return []


def parse_loaded_skills_from_turn_badges(text: str) -> tuple[str, ...]:
    """Parse kebab slugs from ``TurnStatus`` caption text (offline-safe).

    Accepts ``Loaded the <slugs> skill(s)``, ``Used the <slugs> skill(s)``, and
    ``Used <slugs>`` captions, validating every candidate with ``_SLUG_RE``.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        matched = _parse_caption_clause(line)
        if not matched and "," in line:
            for sub in _COMPOUND_CAPTION_SPLIT.split(line):
                matched.extend(_parse_caption_clause(sub))
        for slug in matched:
            if slug not in seen:
                seen.add(slug)
                out.append(slug)
    return tuple(out)


_TURN_STATUS_AND_PANEL_COLLECT_JS = """
  const wm = (window.__ulgTurnStatusIds =
    window.__ulgTurnStatusIds || new WeakMap());
  let seq = window.__ulgTurnStatusSeq || 0;
  const nodeIdFor = (el) => {
    const itemKey = (el.getAttribute('data-item-key') || '').trim();
    if (itemKey) return `item:${itemKey}`;
    let id = wm.get(el);
    if (!id) {
      seq += 1;
      window.__ulgTurnStatusSeq = seq;
      id = `node:${seq}`;
      wm.set(el, id);
    }
    return id;
  };

  const panelRows = Array.from(
    document.querySelectorAll(
      'aside[aria-label="Session activity panel"] [data-kind-row="skills"]'
    )
  );
  const session_labels = [];
  for (const row of panelRows) {
    const al = row.getAttribute('aria-label');
    if (al && al.trim()) session_labels.push(al.trim());
  }

  const captions = [];
  const turnStatuses = Array.from(
    document.querySelectorAll('[data-cds="TurnStatus"]')
  );
  for (const ts of turnStatuses) {
    if (
      ts.closest(
        '[data-testid="user-message"], [data-testid="human-turn"], [contenteditable="true"]'
      ) ||
      ts.matches('[contenteditable="true"]')
    ) {
      continue;
    }
    const node_id = nodeIdFor(ts);
    const morphs = Array.from(
      ts.querySelectorAll('span[data-morph-key]')
    ).filter(
      (el) => !el.closest('[data-cds="TurnStatusStep"], [data-cds-row-panel]')
    );
    if (morphs.length) {
      for (const el of morphs) {
        captions.push({ node_id, text: (el.textContent || '').trim() });
      }
    } else {
      const labelEl = ts.querySelector(':scope > [data-cds-row] [id$="-label"]');
      captions.push({
        node_id,
        text: labelEl ? (labelEl.textContent || '').trim() : '',
      });
    }
  }
"""

_ASSISTANT_TURN_BADGE_JS = (
    "() => {"
    + _TURN_STATUS_AND_PANEL_COLLECT_JS
    + "\n  return { session_labels, captions };\n}"
)

_SKILL_INDUCTION_SNAPSHOT_JS = (
    "() => {"
    + _TURN_STATUS_AND_PANEL_COLLECT_JS
    + """
  const context_skills = [];
  const slugRe = /^[a-z0-9]+(?:-[a-z0-9]+)+$/;
  const headers = Array.from(
    document.querySelectorAll('[data-section-header][role="button"]')
  );
  const ctxHeader = headers.find(
    (el) =>
      ((el.textContent || '').trim().split('\\n')[0] || '').trim() === 'Context'
  );
  if (
    ctxHeader &&
    (ctxHeader.getAttribute('aria-expanded') || '').toLowerCase() === 'true' &&
    ctxHeader.nextElementSibling
  ) {
    const root = ctxHeader.nextElementSibling;
    const skillsHeading = Array.from(
      root.querySelectorAll('span,h1,h2,h3,h4,div,button')
    ).find((n) => (n.textContent || '').trim() === 'Skills');
    if (skillsHeading) {
      let sroot = skillsHeading.parentElement;
      for (let i = 0; i < 6 && sroot; i++) {
        const slugish = Array.from(sroot.querySelectorAll('span,a,button,div,li'))
          .map((n) => (n.textContent || '').trim().toLowerCase())
          .filter((t) => slugRe.test(t));
        if (slugish.length) {
          for (const s of slugish) context_skills.push(s);
          break;
        }
        sroot = sroot.parentElement;
      }
    }
  }
  return { session_labels, captions, context_skills };
}"""
)


def _captions_to_slugs(
    raw_captions: Any,
    *,
    snapshot: SkillInductionSnapshot | None = None,
) -> tuple[str, ...]:
    if not isinstance(raw_captions, list):
        return ()
    slugs: list[str] = []
    for item in raw_captions:
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("node_id") or "").strip()
        if snapshot is not None and (
            not node_id or node_id in snapshot.caption_node_ids
        ):
            continue
        text = str(item.get("text") or "").strip()
        if text:
            slugs.extend(parse_loaded_skills_from_turn_badges(text))
    return _ordered_unique_slugs(tuple(slugs))


async def snapshot_skill_induction_before_submit(
    page: Page,
) -> SkillInductionSnapshot:
    """Capture pre-submit panel slugs and ``TurnStatus`` caption node ids."""
    raw = await page.evaluate(_SKILL_INDUCTION_SNAPSHOT_JS)
    if not isinstance(raw, dict):
        return SkillInductionSnapshot()
    panel: set[str] = set()
    for label in raw.get("session_labels") or []:
        panel.update(parse_session_activity_skills(str(label)))
    for slug in raw.get("context_skills") or []:
        norm = str(slug).strip().lower()
        if _SLUG_RE.fullmatch(norm):
            panel.add(norm)
    node_ids: set[str] = set()
    for item in raw.get("captions") or []:
        if isinstance(item, dict):
            nid = str(item.get("node_id") or "").strip()
            if nid:
                node_ids.add(nid)
    for item in raw.get("caption_node_ids") or []:
        nid = str(item or "").strip()
        if nid:
            node_ids.add(nid)
    return SkillInductionSnapshot(
        panel_slugs=frozenset(panel),
        caption_node_ids=frozenset(node_ids),
    )


async def scrape_chat_turn_loaded_skills(
    page: Page,
    *,
    snapshot: SkillInductionSnapshot | None = None,
) -> tuple[str, ...]:
    """Skill slugs from Session activity panel + ``TurnStatus`` captions.

    Reads only ``aside[aria-label="Session activity panel"] [data-kind-row="skills"]``
    ``aria-label`` and ``[data-cds="TurnStatus"]`` caption elements — never
    assistant ``innerText``, model prose, or ``TurnStatusStep`` subjects/titles.
    When ``snapshot`` is supplied, returns only panel slugs absent from
    ``snapshot.panel_slugs`` or captions on nodes absent from
    ``snapshot.caption_node_ids``.
    """
    raw = await page.evaluate(_ASSISTANT_TURN_BADGE_JS)
    if not isinstance(raw, dict):
        return ()
    session_slugs: list[str] = []
    for label in raw.get("session_labels") or []:
        session_slugs.extend(parse_session_activity_skills(str(label)))
    if snapshot is not None:
        session_slugs = [
            slug for slug in session_slugs if slug not in snapshot.panel_slugs
        ]
    session_tuple = _ordered_unique_slugs(tuple(session_slugs))
    caption_tuple = _captions_to_slugs(raw.get("captions"), snapshot=snapshot)
    return _ordered_unique_slugs(session_tuple + caption_tuple)


def parse_skills_from_context_section(section_text: str) -> tuple[str, ...]:
    """Pure parse of Context-rail ``innerText`` → skill slugs (testable offline)."""
    lines = [ln.strip() for ln in section_text.splitlines() if ln.strip()]
    try:
        skills_idx = next(i for i, ln in enumerate(lines) if ln.lower() == "skills")
    except StopIteration:
        return ()
    out: list[str] = []
    for ln in lines[skills_idx + 1 :]:
        if _SECTION_STOP.match(ln):
            break
        if _SLUG_RE.fullmatch(ln):
            out.append(ln.lower())
    # de-dupe, preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for slug in out:
        if slug not in seen:
            seen.add(slug)
            ordered.append(slug)
    return tuple(ordered)


def require_chat_surface(page: Page) -> str:
    """Fail closed unless the tab is a chat/Cowork compose surface."""
    url = page.url or ""
    if not _COMPOSE_URL.search(url):
        raise ChatContextSkillsError(
            "Context→Skills scrape requires /new|/cowork/cse_|/cowork/local_|/chat/ "
            f"— on {url!r}"
        )
    return url


async def _pick_chat_page(context: BrowserContext, *, chat_url: str | None) -> Page:
    if chat_url:
        want = chat_url.rstrip("/")
        for page in context.pages:
            if page.url.rstrip("/") == want or want in page.url:
                await page.bring_to_front()
                return page
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(chat_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        return page

    for page in context.pages:
        if _COMPOSE_URL.search(page.url or "") and "#settings" not in (page.url or ""):
            await page.bring_to_front()
            return page
    for page in context.pages:
        if "claude.ai" in (page.url or ""):
            await page.bring_to_front()
            return page
    if context.pages:
        return context.pages[0]
    raise ChatContextSkillsError("No browser pages on CDP session")


async def scrape_loaded_skills(
    page: Page,
    *,
    snapshot: SkillInductionSnapshot | None = None,
) -> LoadedSkillsReport:
    """Return the skill slugs listed under the chat UI Context → Skills frame.

    Non-LLM. Does not ask the model. Confirms the Context section header
    is expanded before reading rows. The Skills label sits in that open
    grid; it is not its own toggle. Empty ``skills`` with ``context_found``
    true means that open section has no skills bound. A header that stays
    collapsed raises ``ChatContextSkillsError``. When ``snapshot`` is supplied,
    filters out pre-submit panel slugs and pre-existing ``TurnStatus`` nodes.
    """
    url = require_chat_surface(page)
    try:
        await expand_context_frame(page)
    except ChatContextSkillsError as exc:
        if "not on the rail" not in str(exc):
            raise
        badge_skills = (
            await scrape_chat_turn_loaded_skills(page)
            if snapshot is None
            else await scrape_chat_turn_loaded_skills(page, snapshot=snapshot)
        )
        if badge_skills:
            return LoadedSkillsReport(
                url=url,
                skills=badge_skills,
                context_found=False,
                skills_heading_found=False,
                model_label=None,
                selectors=(
                    *_SELECTORS_USED,
                    _SESSION_ACTIVITY_SELECTOR,
                    _BADGE_SELECTOR,
                ),
                raw_section_text="",
            )
        raise
    await page.wait_for_timeout(400)

    raw = await page.evaluate(
        """() => {
          const slugRe = /^[a-z0-9]+(?:-[a-z0-9]+)+$/;
          const headers = Array.from(
            document.querySelectorAll('[data-section-header][role="button"]')
          );
          const ctxHeader = headers.find((el) =>
            (el.innerText || '').trim().split('\\n')[0].trim() === 'Context'
          );
          const spans = Array.from(document.querySelectorAll('span'));
          const ctxSpan =
            spans.find((s) => {
              const t = (s.textContent || '').trim();
              if (t !== 'Context') return false;
              return (s.className || '').toString().includes('font-medium');
            }) || spans.find((s) => (s.textContent || '').trim() === 'Context');
          if (!ctxHeader && !ctxSpan) {
            return {
              context_found: false,
              skills_heading_found: false,
              skills: [],
              raw_section_text: '',
              model_label: null,
            };
          }

          let root = null;
          if (ctxHeader && ctxHeader.nextElementSibling) {
            root = ctxHeader.nextElementSibling;
          } else if (ctxSpan) {
            root = ctxSpan.parentElement;
          }
          const text = root ? (root.innerText || '').trim() : '';
          const lines = text.split('\\n').map((l) => l.trim()).filter(Boolean);

          const skillsIdx = lines.findIndex((l) => /^Skills$/i.test(l));
          const fromLines = [];
          if (skillsIdx >= 0) {
            for (let i = skillsIdx + 1; i < lines.length; i++) {
              const l = lines[i];
              if (/^(Progress|Outputs|Context|Files|Connectors|Memory|Artifacts)$/i.test(l)) break;
              if (slugRe.test(l)) fromLines.push(l.toLowerCase());
            }
          }

          const skillsHeading = root
            ? Array.from(root.querySelectorAll('span,h1,h2,h3,h4,div,button')).find(
                (n) => (n.textContent || '').trim() === 'Skills'
              )
            : null;

          let fromDom = [];
          if (skillsHeading) {
            let sroot = skillsHeading.parentElement;
            for (let i = 0; i < 6 && sroot; i++) {
              const slugish = Array.from(sroot.querySelectorAll('span,a,button,div,li'))
                .map((n) => (n.textContent || '').trim())
                .filter((t) => slugRe.test(t));
              if (slugish.length) {
                fromDom = [...new Set(slugish.map((s) => s.toLowerCase()))];
                break;
              }
              sroot = sroot.parentElement;
            }
          }

          const skills = fromDom.length ? fromDom : [...new Set(fromLines)];

          const body = document.body.innerText || '';
          const modelMatch = body.match(
            /(?:Fable|Opus|Sonnet|Claude)\\s*\\d[^\\n]{0,40}/
          );

          return {
            context_found: true,
            skills_heading_found: skillsIdx >= 0 || !!skillsHeading,
            skills,
            raw_section_text: text.slice(0, 800),
            model_label: modelMatch ? modelMatch[0].trim() : null,
          };
        }"""
    )

    skills = tuple(
        s
        for s in (raw.get("skills") or [])
        if isinstance(s, str) and _SLUG_RE.fullmatch(s)
    )
    if snapshot is not None and (snapshot.panel_slugs or snapshot.caption_node_ids):
        fresh_panel = tuple(s for s in skills if s not in snapshot.panel_slugs)
        raw_turn = await page.evaluate(_ASSISTANT_TURN_BADGE_JS)
        fresh_captions = (
            _captions_to_slugs(raw_turn.get("captions"), snapshot=snapshot)
            if isinstance(raw_turn, dict)
            else ()
        )
        skills = _ordered_unique_slugs(fresh_panel + fresh_captions)
    return LoadedSkillsReport(
        url=url,
        skills=skills,
        context_found=bool(raw.get("context_found")),
        skills_heading_found=bool(raw.get("skills_heading_found")),
        model_label=raw.get("model_label"),
        selectors=_SELECTORS_USED,
        raw_section_text=str(raw.get("raw_section_text") or "")[:800],
    )


async def scrape_loaded_skills_cdp(
    *,
    cdp_url: str = DEFAULT_CDP_URL,
    chat_url: str | None = None,
) -> LoadedSkillsReport:
    """Connect over CDP, pick a chat page, scrape Context → Skills."""
    pw, _browser, context, _page = await connect_cdp(cdp_url)
    try:
        page = await _pick_chat_page(context, chat_url=chat_url)
        if chat_url and urlparse(page.url).path != urlparse(chat_url).path:
            await page.goto(chat_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
        return await scrape_loaded_skills(page)
    finally:
        await pw.stop()


def print_loaded_skills_report(
    report: LoadedSkillsReport,
    *,
    required: list[str] | None = None,
) -> int:
    """Emit human-readable scrape result; exit 0 iff required ⊆ skills (or no required)."""
    print(f"chat_url={report.url}", flush=True)
    print(f"context_found={report.context_found}", flush=True)
    print(f"skills_heading_found={report.skills_heading_found}", flush=True)
    if report.model_label:
        print(f"model_label={report.model_label}", flush=True)
    print(f"loaded_skills ({len(report.skills)}):", flush=True)
    if report.skills:
        for slug in report.skills:
            print(f"  {slug}", flush=True)
    else:
        print("  (none)", flush=True)
    print("selectors:", flush=True)
    for sel in report.selectors:
        print(f"  - {sel}", flush=True)

    if not report.context_found:
        print("FAIL: Context frame not found", flush=True)
        return 2
    if required:
        missing = report.missing(required)
        if missing:
            print(f"missing_required ({len(missing)}):", flush=True)
            for slug in missing:
                print(f"  {slug}", flush=True)
            return 1
        print("OK required skills present in Context frame", flush=True)
    return 0


def emit_loaded_skills_json(
    report: LoadedSkillsReport,
    *,
    required: list[str] | None = None,
) -> int:
    """Print the Context → Skills observation as JSON and return an exit code.

    ``required`` adds ``missing_required`` so a CLI caller can see which
    slugs the open list did not contain. Returns 2 when the Context frame
    is absent, 1 when a required slug is missing, and 0 when the report
    is usable.
    """
    payload = report.to_dict()
    payload["skills"] = list(report.skills)
    payload["selectors"] = list(report.selectors)
    if required is not None:
        payload["required"] = list(required)
        payload["missing_required"] = list(report.missing(required))
        payload["required_ok"] = not payload["missing_required"]
    print(json.dumps(payload, indent=2))
    if not report.context_found:
        return 2
    if required and report.missing(required):
        return 1
    return 0
