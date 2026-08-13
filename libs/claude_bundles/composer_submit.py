"""Prove Cowork/Chat composer submit by draft-clear, not click self-report.

Warm follow-up historically clicked Send with ``force=True`` and returned.
Playwright then treated a DOM snippet as ``dom_paste`` and reloaded the page to
upgrade receipt — wiping an unsent draft and cancelling an in-flight POST.
This module is the honesty gate: the needle must leave the composer.
"""

from __future__ import annotations

import re
from typing import Any

_UNIQUE_MARKER_RE = re.compile(r"#\d+-unique:\s*\S+")
_RELEASE_POLL_MS = 200
_RELEASE_TIMEOUT_S = 3.0
_SETTLE_MS = 1200
_CLICK_TIMEOUT_MS = 2500

_COMPOSER_DRAFT_JS = """() => {
  const composer = document.querySelector('[data-testid="chat-input"]')
    || document.querySelector('[contenteditable="true"][data-testid]')
    || document.querySelector('[contenteditable="true"]')
    || document.querySelector('[role="textbox"]');
  if (!composer) {
    return { ok: false, text: '', len: 0 };
  }
  const t = (composer.innerText || composer.value || '').trim();
  return { ok: true, text: t.slice(0, 800), len: t.length };
}"""


def verification_marker(prompt: str) -> str:
    """Distinctive substring that must leave the composer after submit.

    Prefer ``#N-unique:…``; else a mid-body slice so shared headers alone
    cannot verify. Empty prompt yields an empty needle.
    """
    text = (prompt or "").strip()
    if not text:
        return ""
    match = _UNIQUE_MARKER_RE.search(text)
    if match:
        return match.group(0)
    if len(text) >= 120:
        return text[40:120].strip()
    return text[:80]


def composer_holds_needle(draft: dict[str, Any], needle: str) -> bool:
    """True when the submit needle is still sitting in composer inner text."""
    if not needle:
        return False
    return needle in str(draft.get("text") or "")


async def read_composer_draft(page) -> dict[str, Any]:
    """Return a composer inner-text snapshot as ``{ok, text, len}``."""
    raw = await page.evaluate(_COMPOSER_DRAFT_JS)
    if not isinstance(raw, dict):
        return {"ok": False, "text": "", "len": 0}
    return {
        "ok": bool(raw.get("ok")),
        "text": str(raw.get("text") or ""),
        "len": int(raw.get("len") or 0),
    }


async def composer_holds_draft(page, needle: str) -> bool:
    """True when the submit needle is still in the composer."""
    if not needle:
        return False
    draft = await read_composer_draft(page)
    return composer_holds_needle(draft, needle)


async def await_composer_released(
    page,
    needle: str,
    *,
    timeout_s: float = _RELEASE_TIMEOUT_S,
    poll_ms: int = _RELEASE_POLL_MS,
) -> bool:
    """Poll until *needle* is absent from the composer, or timeout."""
    if not needle:
        return True
    elapsed = 0.0
    limit_ms = int(timeout_s * 1000)
    if not await composer_holds_draft(page, needle):
        return True
    while elapsed < limit_ms:
        await page.wait_for_timeout(poll_ms)
        elapsed += poll_ms
        if not await composer_holds_draft(page, needle):
            return True
    return False


async def click_submit_button(btn) -> bool:
    """Click an enabled submit control; actionable click first, force fallback.

    ``force=True`` alone is how Cowork Send became a no-op: Playwright reports
    success while the SPA never posts. Try a real actionability click first.
    """
    if not await btn.is_visible() or await btn.is_disabled():
        return False
    try:
        await btn.click(timeout=_CLICK_TIMEOUT_MS)
        return True
    except Exception:
        await btn.click(force=True, timeout=_CLICK_TIMEOUT_MS)
        return True


async def press_send_chords(page, needle: str = "") -> None:
    """Send chords after a click that left the draft in the box.

    Bare Enter is refused (multiline contenteditable inserts a newline).
    Control+Enter then Meta+Enter are the product send shortcuts.
    """
    await page.keyboard.press("Control+Enter")
    if needle and not await composer_holds_draft(page, needle):
        return
    await page.wait_for_timeout(250)
    await page.keyboard.press("Meta+Enter")


async def prove_composer_submitted(page, prompt: str) -> None:
    """Fail closed unless the prompt needle leaves the composer.

    After the caller has clicked Send, wait for draft-clear. If the needle
    remains, retry Control/Meta+Enter. Still present ⇒ raise — never reload.
    """
    needle = verification_marker(prompt)
    if not needle:
        return
    if await await_composer_released(page, needle):
        return
    await press_send_chords(page, needle)
    if await await_composer_released(page, needle):
        return
    raise RuntimeError(
        "submit did not clear composer: Send click/chords left the draft in the box"
    )


async def marker_survives_settle(page, needle: str, *, settle_ms: int = _SETTLE_MS) -> bool:
    """True when *needle* remains in committed-turn nodes after a short settle.

    Replaces page.reload() as the ``dom_committed`` upgrade. Reload of an
    unsent draft is the paste-not-submitted failure class.
    """
    if not needle:
        return False
    if settle_ms > 0:
        await page.wait_for_timeout(settle_ms)
    return bool(await page.evaluate(_MARKER_IN_COMMITTED_JS, needle))


_MARKER_IN_COMMITTED_JS = """
(marker) => {
  function excluded(el) {
    if (!el) return true;
    if (el.isContentEditable) return true;
    if (el.closest('[contenteditable="true"]')) return true;
    const testid = (el.getAttribute('data-testid') || '').toLowerCase();
    if (testid.includes('composer') || testid.includes('input')) return true;
    if (el.getAttribute('role') === 'textbox') return true;
    return false;
  }
  const selectors = [
    '[data-testid="user-message"]',
    '[data-testid="human-turn"]',
    'div[class*="font-user"]',
  ];
  for (const sel of selectors) {
    for (const el of document.querySelectorAll(sel)) {
      if (excluded(el)) continue;
      const t = (el.innerText || '').trim();
      if (t && t.includes(marker)) return true;
    }
  }
  return false;
}
"""
