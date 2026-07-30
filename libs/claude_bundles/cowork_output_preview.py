"""Filename-button Outputs preview extract for Cowork harvest.

Cowork Outputs rows sometimes render as plain ``<button>`` labels (filename
only) — no ``download`` attr / aria-label. Click opens a preview panel; the
deliverable body is recoverable from the deepest large text container.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from playwright.async_api import Page

if TYPE_CHECKING:
    from claude_bundles.cowork_output_download import OutputDownloadResult

# Extensions observed on Cowork Outputs rows (arc 6386 filename-button miss).
_OUTPUT_FILENAME_RE = re.compile(
    r"(?i)^[\w][\w.\- ]{0,180}\.(md|txt|json|ya?ml|toml|csv|py|html?|pdf|"
    r"ts|tsx|js|jsx|rs|go|sh)$"
)

# Preview chrome / empty panels are far below a real deliverable body.
MIN_PREVIEW_BODY_CHARS = 120

_OUTPUT_FILENAME_BUTTON_JS = """
() => {
  const FILE_RE = /\\.(md|txt|json|ya?ml|toml|csv|py|html?|pdf|ts|tsx|js|jsx|rs|go|sh)$/i;
  const root = document.body;
  if (!root) return null;

  const scopes = [];
  for (const el of root.querySelectorAll(
    'aside, nav, section, [role="complementary"], [role="navigation"], div'
  )) {
    const head = (el.textContent || '').slice(0, 800);
    if (!/\\boutputs?\\b/i.test(head)) continue;
    if (head.length > 12000) continue;
    scopes.push(el);
  }
  if (!scopes.length) scopes.push(root);

  const candidates = [];
  const push = (el, label, score) => {
    if (!el || el.disabled) return;
    candidates.push({ el, label, score });
  };

  for (const scope of scopes) {
    const inOutputs = scope !== root;
    for (const el of scope.querySelectorAll('button, [role="button"], a')) {
      if (el.hasAttribute('download')) continue;
      const aria = (el.getAttribute('aria-label') || '').toLowerCase();
      if (aria.includes('download')) continue;
      const label = (el.innerText || el.textContent || '')
        .trim().replace(/\\s+/g, ' ');
      if (!label || label.length > 200) continue;
      if (!FILE_RE.test(label)) continue;
      // Reject labels that are clearly UI chrome, not a filename row.
      if (/^(download|copy|open|delete)\\b/i.test(label)) continue;
      let score = 20;
      if (inOutputs) score += 40;
      if (/\\.md$/i.test(label)) score += 15;
      if (label.length <= 80) score += 15;
      push(el, label, score);
    }
  }
  candidates.sort((a, b) => b.score - a.score);
  if (!candidates.length) return null;
  const best = candidates[0];
  best.el.setAttribute('data-cdp-output-filename', '1');
  return { tagged: true, filename: best.label, score: best.score };
}
"""

_OUTPUT_PREVIEW_EXTRACT_JS = """
() => {
  const MIN = 120;
  const chromey = (t) => {
    const s = (t || '').trim();
    if (!s) return true;
    const lower = s.toLowerCase();
    if (lower.includes('write your prompt')) return true;
    if (s.length < 240 && lower.includes('click to collapse')) return true;
    if (/^(copy|outputs?|files?|more ways to open)$/i.test(s)) return true;
    return false;
  };

  const candidates = [];
  for (const el of document.body.querySelectorAll(
    'pre, article, section, main, div, [role="article"], [role="document"]'
  )) {
    const text = (el.innerText || '').trim();
    if (text.length < MIN) continue;
    if (chromey(text)) continue;
    let score = text.length;
    if (/^#{1,6}\\s/m.test(text)) score += 5000;
    if (/\\bverdict\\b/i.test(text)) score += 2000;
    if (/^---$/m.test(text)) score += 500;
    // Prefer deeper containers (fewer descendants) among large text nodes.
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


def looks_like_output_filename(label: str) -> bool:
    """Return whether *label* looks like a Cowork Outputs filename row."""
    text = (label or "").strip()
    if not text or len(text) > 200:
        return False
    return bool(_OUTPUT_FILENAME_RE.match(text))


def is_thin_or_chrome_preview(text: str) -> bool:
    """Reject empty, short, or chrome-dominated preview extractions."""
    body = (text or "").strip()
    if len(body) < MIN_PREVIEW_BODY_CHARS:
        return True
    lower = body.lower()
    if "write your prompt" in lower:
        return True
    if len(body) < 240 and "click to collapse" in lower:
        return True
    if lower in {"copy", "output", "outputs", "files", "more ways to open"}:
        return True
    return False


async def extract_cowork_output_preview(
    page: Page,
    *,
    settle_ms: int = 750,
) -> OutputDownloadResult | None:
    """Click a filename-button Outputs row and extract preview body text.

    Returns ``None`` when no filename button is found or the preview body looks
    like chrome / empty. Does not use ``expect_download``.
    """
    # Late import avoids cycle with cowork_output_download orchestration.
    from claude_bundles.cowork_output_download import OutputDownloadResult

    tagged = await page.evaluate(_OUTPUT_FILENAME_BUTTON_JS)
    if not tagged:
        return None
    filename = (tagged.get("filename") or "").strip() or "cowork-output"
    locator = page.locator("[data-cdp-output-filename='1']").first
    if not await locator.count():
        return None
    await locator.click(force=True)
    if settle_ms > 0:
        await page.wait_for_timeout(settle_ms)
    extracted = await page.evaluate(_OUTPUT_PREVIEW_EXTRACT_JS)
    if not extracted:
        return None
    content = (extracted.get("content") or "").strip()
    if is_thin_or_chrome_preview(content):
        return None
    raw = content.encode("utf-8")
    return OutputDownloadResult(
        filename=filename,
        content=content,
        content_bytes=raw,
    )


__all__ = [
    "MIN_PREVIEW_BODY_CHARS",
    "extract_cowork_output_preview",
    "is_thin_or_chrome_preview",
    "looks_like_output_filename",
]
