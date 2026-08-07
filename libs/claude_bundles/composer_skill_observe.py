"""DOM observation of attached Claude skills — the substrate side of attach.

``attach_session_skills`` reports the slugs whose *click sequence* completed
without raising. A completed click is not a landed skill: the Customize list is
long and the picker can silently no-op, so the clicker's return value is a
self-report, not an artifact. Everything here reads the page instead.

This module owns one surface: the **composer chip**. Attaching inserts a TipTap
node ``span.react-renderer.node-skillChip`` into ``[data-testid="chat-input"]``,
carrying the slug in a ``span.select-none`` (a sibling ``span[aria-hidden]``
holds the ``/`` glyph). It is the gate because it is observable *before* submit,
so a failed attach costs a retry rather than a skill-blind turn. Selectors come
from a live read-only CDP probe of Jupiter Chrome, not from a screenshot.

The post-submit receipt — the session view's right-hand **Context → Skills**
list — lives in :mod:`claude_bundles.chat_context_skills`
(``scrape_loaded_skills``). It cannot gate a submit: bare ``/new`` renders no
Context anchor at all (probed 2026-08-06), so an empty read there means
"panel absent", not "nothing loaded".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from playwright.async_api import Page

from claude_bundles.cowork_skill_delivery import SkillDeliveryError

DEFAULT_ATTEMPTS = 3
_SETTLE_MS = 400

_COMPOSER_CHIPS_JS = r"""() => {
  const composer = document.querySelector('[data-testid="chat-input"]')
    || document.querySelector('[contenteditable="true"][role="textbox"]')
    || document.querySelector('[contenteditable="true"]');
  if (!composer) return {ok: false, reason: 'composer_missing', slugs: []};
  const slugs = [];
  for (const chip of composer.querySelectorAll('.node-skillChip')) {
    let label = '';
    for (const span of chip.querySelectorAll('span.select-none')) {
      if (span.getAttribute('aria-hidden') === 'true') continue;
      const text = (span.textContent || '').trim();
      if (text && text !== '/') { label = text; break; }
    }
    if (!label) {
      label = (chip.innerText || '').replace(/^[\s/]+/, '').trim();
    }
    if (label) slugs.push(label);
  }
  return {ok: true, slugs};
}"""


@dataclass(frozen=True)
class SkillAttachObservation:
    """What the page shows after an attach pass — not what the clicker claims."""

    requested: tuple[str, ...]
    observed: tuple[str, ...]
    missing: tuple[str, ...]
    attempts: int
    surface: str = "composer_chip"

    @property
    def complete(self) -> bool:
        return not self.missing

    def as_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "requested": list(self.requested),
            "observed": list(self.observed),
            "missing": list(self.missing),
            "attempts": self.attempts,
        }


async def observe_composer_skill_chips(page: Page) -> list[str]:
    """Slugs currently rendered as skill chips inside the composer.

    Raises ``SkillDeliveryError`` when the composer itself is absent — an
    unreadable surface must not be reported as "no skills attached".
    """
    result = await page.evaluate(_COMPOSER_CHIPS_JS)
    if not result or not result.get("ok"):
        reason = (result or {}).get("reason", "unknown")
        raise SkillDeliveryError(
            f"cannot observe composer skill chips: {reason} (url={page.url!r})"
        )
    return [str(slug) for slug in result.get("slugs", [])]


async def attach_skills_verified(
    page: Page,
    slugs: list[str],
    *,
    composer,
    attempts: int = DEFAULT_ATTEMPTS,
    settle_ms: int = _SETTLE_MS,
) -> SkillAttachObservation:
    """Attach each slug and confirm it against the DOM before moving on.

    Re-observes between rounds and only retries what the page still does not
    show, so a partially-landed set costs one extra click per missing slug
    rather than a redundant full re-attach. Returns the observation; the caller
    owns the fail-closed decision (``attest_delivery_channels``) so channel
    policy stays in one place.
    """
    from claude_bundles.composer_session_skills import attach_one_session_skill

    requested = tuple(str(s).strip() for s in slugs if str(s).strip())
    if not requested:
        return SkillAttachObservation((), (), (), 0)

    observed: list[str] = []
    for attempt in range(1, max(1, attempts) + 1):
        observed = await observe_composer_skill_chips(page)
        missing = [slug for slug in requested if slug not in observed]
        if not missing:
            return SkillAttachObservation(requested, tuple(observed), (), attempt)
        for slug in missing:
            try:
                await attach_one_session_skill(page, slug, composer=composer)
            except SkillDeliveryError:
                continue
            await page.wait_for_timeout(settle_ms)

    observed = await observe_composer_skill_chips(page)
    missing = tuple(slug for slug in requested if slug not in observed)
    return SkillAttachObservation(requested, tuple(observed), missing, max(1, attempts))
