"""Scripted scrape of claude.ai chat UI Context → Skills (non-LLM).

Operator correction (arc 6895): a model's ``SKILLS_PROBE_OK`` self-report is not
evidence that Customize skills are session-loaded. The chat right-rail
**Context** frame lists the skills the product actually bound. This module
reads that DOM — deterministic, zero tokens.

Loci (live 2026-08-07, Cowork CSE ``cse_01AhPKZ5C8gb1py1m3xHbEeL``):

- Heading: ``span.font-medium.text-sm.text-primary`` with exact text ``Context``
- Section siblings in the right rail: Progress / Outputs / Context
- Under Context, a ``Skills`` group (container class often ``mb-2 last:mb-0``)
  whose children are kebab-case skill slugs
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Page

from claude_bundles.skills_ui_panel import DEFAULT_CDP_URL, connect_cdp

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+$")
_COMPOSE_URL = re.compile(r"/new(?:\?|$|#)|/cowork/cse_|/chat/", re.I)

# Right-rail section labels — stop collecting when another section begins.
_SECTION_STOP = re.compile(
    r"^(Progress|Outputs|Context|Files|Connectors|Memory|Artifacts)$", re.I
)


class ChatContextSkillsError(RuntimeError):
    """Context frame scrape failed closed."""


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


_SELECTORS_USED = (
    'span.font-medium.text-sm.text-primary:text-is("Context")',
    "right-rail section: Progress|Outputs|Context",
    "under Context: exact heading Skills",
    "skill rows: kebab-case slug text under Skills group (div.mb-2.last:mb-0)",
)


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
            f"Context→Skills scrape requires /new|/cowork/cse_|/chat/ — on {url!r}"
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


async def expand_context_frame(page: Page) -> bool:
    """Click the Context rail control if present. Returns whether it was found."""
    return bool(
        await page.evaluate(
            """() => {
              const spans = Array.from(document.querySelectorAll('span'));
              const ctx = spans.find((s) => {
                const t = (s.textContent || '').trim();
                if (t !== 'Context') return false;
                const cls = (s.className || '').toString();
                return cls.includes('font-medium') || cls.includes('text-sm');
              }) || spans.find((s) => (s.textContent || '').trim() === 'Context');
              if (!ctx) return false;
              const btn = ctx.closest('button, [role="button"]') || ctx.parentElement;
              if (btn) { btn.click(); return true; }
              return false;
            }"""
        )
    )


async def scrape_loaded_skills(page: Page) -> LoadedSkillsReport:
    """Return the skill slugs listed under the chat UI Context → Skills frame.

    Non-LLM. Does not ask the model. Empty ``skills`` with ``context_found``
    true means the frame is open but no skills are bound.
    """
    url = require_chat_surface(page)
    await expand_context_frame(page)
    await page.wait_for_timeout(400)

    raw = await page.evaluate(
        """() => {
          const slugRe = /^[a-z0-9]+(?:-[a-z0-9]+)+$/;
          const spans = Array.from(document.querySelectorAll('span'));
          const ctxSpan = spans.find((s) => {
            const t = (s.textContent || '').trim();
            if (t !== 'Context') return false;
            return (s.className || '').toString().includes('font-medium');
          }) || spans.find((s) => (s.textContent || '').trim() === 'Context');
          if (!ctxSpan) {
            return {
              context_found: false,
              skills_heading_found: false,
              skills: [],
              raw_section_text: '',
              model_label: null,
            };
          }

          // Smallest ancestor whose text has Context + Skills but stays in the
          // right rail (avoid swallowing the whole transcript).
          let best = null;
          let el = ctxSpan;
          for (let i = 0; i < 14 && el; i++) {
            const t = (el.innerText || '').trim();
            if (/^Context\\b/m.test(t) && /\\bSkills\\b/.test(t) && t.length < 2500) {
              // Prefer nodes that do not contain the chat transcript cues.
              const looksLikeRail = !/You said:/i.test(t) && !/TYPE:\\s*PROBE/i.test(t);
              if (looksLikeRail || best === null) best = el;
              if (looksLikeRail && t.length < 800) break;
            }
            el = el.parentElement;
          }
          const root = best || ctxSpan.parentElement;
          const text = (root.innerText || '').trim();
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

          const skillsHeading = Array.from(
            root.querySelectorAll('span,h1,h2,h3,h4,div,button')
          ).find((n) => (n.textContent || '').trim() === 'Skills');

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

          // Prefer DOM-local Skills group; fall back to line parse.
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
