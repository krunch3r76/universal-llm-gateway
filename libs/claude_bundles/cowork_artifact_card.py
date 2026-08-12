"""In-chat Cowork Document/MD artifact card body extraction for CDP harvest.

Sibling to ``cowork_output_preview`` — click card → panel/canvas ``innerText``
(or copy affordance) → ``ArtifactCardResult``. Returns ``None`` on miss and on
empty/chrome-only extract (title echoed without body). Card-toolbar ``Google Drive``
is export-dropdown trigger chrome (optional Drive ∨ Download); fleet never uses Drive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from playwright.async_api import Page

# Preview chrome / empty panels are far below a real deliverable body.
MIN_CARD_BODY_CHARS = 120

_CARD_KIND_RE = re.compile(r"\bdocument\s*[·•]\s*md\b", re.IGNORECASE)

_ARTIFACT_CARD_TAG_JS = """
(title) => {
  const want = (title || '').trim().toLowerCase();
  if (!want) return null;
  const nodes = document.querySelectorAll('[data-cdp-artifact-card]');
  for (const el of nodes) {
    const raw = (el.innerText || el.textContent || '').trim();
    const aria = (el.getAttribute('aria-label') || '').trim();
    const hay = (raw + ' ' + aria).toLowerCase();
    if (hay.includes(want)) {
      el.setAttribute('data-cdp-artifact-card-target', '1');
      return { tagged: true, title: title };
    }
  }
  // Fallback: scan buttons/links in the last assistant turn for title match.
  const selectors = [
    '[data-testid="assistant-message"]',
    '[data-testid="assistant-turn"]',
    'div[class*="font-claude"]',
    '[data-testid*="assistant"]',
    '[class*="AssistantMessage"]',
    'article[class*="message"]',
    '[role="article"]',
  ];
  const turns = [];
  const seen = new Set();
  for (const sel of selectors) {
    for (const el of document.querySelectorAll(sel)) {
      if (seen.has(el)) continue;
      seen.add(el);
      const t = (el.innerText || '').trim();
      if (/^You said:\\s*/i.test(t)) continue;
      if (t.length > 40) turns.push(el);
    }
  }
  const lastTurn = turns.length ? turns[turns.length - 1] : null;
  if (!lastTurn) return null;
  for (const el of lastTurn.querySelectorAll('button, a, [role="button"]')) {
    const raw = (el.innerText || el.textContent || '').trim();
    const aria = (el.getAttribute('aria-label') || '').trim();
    const hay = (raw + ' ' + aria).toLowerCase();
    if (!hay.includes(want)) continue;
    if (!/\\bdocument\\b/i.test(hay) && !/\\.md\\b/i.test(hay)) continue;
    el.setAttribute('data-cdp-artifact-card-target', '1');
    return { tagged: true, title: title };
  }
  return null;
}
"""

_ARTIFACT_CARD_EXTRACT_JS = """
(title) => {
  const MIN = 120;
  const want = (title || '').trim().toLowerCase();
  const chromey = (t) => {
    const s = (t || '').trim();
    if (!s) return true;
    const lower = s.toLowerCase();
    if (lower.includes('write your prompt')) return true;
    if (s.length < 240 && lower.includes('click to collapse')) return true;
    if (/^(copy|outputs?|files?|more ways to open|google drive)$/i.test(s)) return true;
    if (want && s.toLowerCase() === want) return true;
    if (/^document\\s*[·•]\\s*md$/i.test(s)) return true;
    return false;
  };

  const candidates = [];
  for (const el of document.body.querySelectorAll(
    'pre, article, section, main, div, [role="article"], [role="document"], '
    + '[class*="canvas" i], [class*="Canvas" i], [class*="artifact" i], [class*="Artifact" i]'
  )) {
    const text = (el.innerText || '').trim();
    if (text.length < MIN) continue;
    if (chromey(text)) continue;
    let score = text.length;
    if (/^#{1,6}\\s/m.test(text)) score += 5000;
    if (/\\bverdict\\b/i.test(text)) score += 2000;
    if (/^---$/m.test(text)) score += 500;
    score -= el.querySelectorAll('*').length;
    if (/write your prompt/i.test(text)) score -= 10000;
    candidates.push({ text, score, len: text.length });
  }
  candidates.sort((a, b) => b.score - a.score);
  if (!candidates.length) return null;
  const best = candidates[0];
  if (best.len < MIN) return null;
  return { content: best.text, length: best.len };
}
"""


@dataclass(frozen=True)
class ArtifactCardResult:
    """Extracted in-chat artifact card body."""

    title: str
    content: str
    kind: str = "MD"


def is_chrome_only_card_extract(title: str, content: str) -> bool:
    """Return whether *content* is empty or title-echo/chrome without body."""
    body = (content or "").strip()
    title_norm = (title or "").strip()
    if len(body) < MIN_CARD_BODY_CHARS:
        return True
    lower = body.lower()
    if "write your prompt" in lower:
        return True
    if len(body) < 240 and "click to collapse" in lower:
        return True
    if lower in {"copy", "output", "outputs", "files", "more ways to open", "google drive"}:
        return True
    if _CARD_KIND_RE.search(body) and len(body) < 240:
        return True
    if title_norm and body.strip().lower() == title_norm.lower():
        return True
    # Title echoed as sole substantive line with connector chrome only.
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    non_chrome = [
        ln
        for ln in lines
        if not re.match(r"^(google drive|download|copy|open|document\s*[·•]\s*md)$", ln, re.I)
    ]
    if len(non_chrome) == 1 and non_chrome[0].lower() == title_norm.lower():
        return True
    return False


def format_labeled_card_section(result: ArtifactCardResult) -> str:
    """Return a labeled archive section for one extracted card."""
    return f"## Artifact card: {result.title}\n\n{result.content.strip()}\n"


def combine_chat_and_card_sections(
    chat_body: str,
    cards: list[ArtifactCardResult],
) -> str:
    """Merge chat prose with labeled artifact card section(s)."""
    parts = [(chat_body or "").strip()]
    for card in cards:
        parts.append(format_labeled_card_section(card))
    return "\n\n".join(p for p in parts if p)


async def extract_artifact_card_body(
    page: Page,
    title: str,
    *,
    settle_ms: int = 750,
) -> ArtifactCardResult | None:
    """Click an in-chat artifact card and extract its panel/canvas body.

    Returns ``None`` when no card matches *title*, or when the extract is
    empty/chrome-only (title echoed without body).
    """
    title_norm = (title or "").strip()
    if not title_norm:
        return None
    tagged = await page.evaluate(_ARTIFACT_CARD_TAG_JS, title_norm)
    if not tagged:
        return None
    locator = page.locator("[data-cdp-artifact-card-target='1']").first
    if not await locator.count():
        return None
    await locator.click(force=True)
    if settle_ms > 0:
        await page.wait_for_timeout(settle_ms)
    extracted = await page.evaluate(_ARTIFACT_CARD_EXTRACT_JS, title_norm)
    if not extracted:
        return None
    content = (extracted.get("content") or "").strip()
    if is_chrome_only_card_extract(title_norm, content):
        return None
    return ArtifactCardResult(title=title_norm, content=content, kind="MD")


__all__ = [
    "MIN_CARD_BODY_CHARS",
    "ArtifactCardResult",
    "combine_chat_and_card_sections",
    "extract_artifact_card_body",
    "format_labeled_card_section",
    "is_chrome_only_card_extract",
]
