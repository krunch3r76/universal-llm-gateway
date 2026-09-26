"""Open the Cowork Context rail and Skills list before a slug scrape.

``scrape_loaded_skills`` calls ``expand_context_frame``. The rail controls
are toggles: a click on an already-open list closes it, and a scrape of a
closed list looks like no skills. This module reads ``aria-expanded`` and
whether the list body is on screen, clicks only when the list is closed,
and refuses the scrape when the list is still closed after that attempt.
"""

from __future__ import annotations

from typing import Any

from playwright.async_api import Page

_DISCLOSURE_JS = """
(spec) => {
  const label = spec.label;
  const doClick = !!spec.click;
  const exact = (el, want) => {
    const own = Array.from(el.childNodes)
      .filter((n) => n.nodeType === Node.TEXT_NODE)
      .map((n) => n.textContent || '')
      .join('')
      .trim();
    if (own === want) return true;
    return el.childElementCount === 0 && (el.textContent || '').trim() === want;
  };
  const nodes = Array.from(
    document.querySelectorAll('span,button,div,h1,h2,h3,h4,summary')
  );
  const node = nodes.find((el) => exact(el, label));
  if (!node) {
    return { found: false, aria_expanded: null, list_visible: false, clicked: false };
  }
  const btn = node.closest('button, [role="button"], summary');
  const firstLine = btn ? (btn.innerText || '').trim().split('\\n')[0].trim() : '';
  const named = !!(btn && (
    (btn.getAttribute('aria-label') || '').trim() === label
    || exact(btn, label)
    || firstLine === label
  ));
  const control = named ? btn : null;
  if (doClick) {
    if (!control) return { found: true, aria_expanded: null, list_visible: false, clicked: false };
    control.click();
    return { found: true, aria_expanded: null, list_visible: false, clicked: true };
  }
  const aria = control ? control.getAttribute('aria-expanded') : null;
  const controlsId = control && control.getAttribute('aria-controls');
  const panel = controlsId ? document.getElementById(controlsId) : null;
  const sibling = (control || node).nextElementSibling;
  const shown = (el) => {
    if (!el) return false;
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    if (el.hasAttribute('hidden')) return false;
    return el.getClientRects().length > 0;
  };
  let listVisible = false;
  if (panel) listVisible = shown(panel);
  else if (sibling) listVisible = shown(sibling);
  else if (label === 'Context') {
    const skillsNode = nodes.find((el) => exact(el, 'Skills'));
    listVisible = !!(skillsNode && shown(skillsNode));
  }
  return {
    found: true,
    aria_expanded: aria,
    list_visible: listVisible,
    clicked: false,
  };
}
"""


def disclosure_is_expanded(
    aria_expanded: str | None,
    *,
    list_visible: bool,
) -> bool:
    """Whether a Context or Skills disclosure is open for a scrape.

    ``aria-expanded="false"`` is closed even when leftover text is in the
    DOM. ``"true"`` is open. With no attribute, the list body must be on
    screen — a missing flag is not an open list. An already-open control
    must not be clicked: that control is a toggle and the click would hide
    the rows about to be read.
    """
    flag = (aria_expanded or "").strip().lower()
    if flag == "true":
        return True
    if flag == "false":
        return False
    return bool(list_visible)


def _refuse(message: str) -> None:
    from claude_bundles.chat_context_skills import ChatContextSkillsError

    raise ChatContextSkillsError(message)


def _aria(state: dict[str, Any]) -> str | None:
    raw = state.get("aria_expanded")
    return raw if isinstance(raw, str) else None


async def _read_disclosure(page: Page, label: str) -> dict[str, Any]:
    raw = await page.evaluate(_DISCLOSURE_JS, {"label": label, "click": False})
    if not isinstance(raw, dict):
        return {"found": False, "aria_expanded": None, "list_visible": False, "clicked": False}
    return raw


async def _require_disclosure_open(page: Page, label: str) -> None:
    state = await _read_disclosure(page, label)
    if disclosure_is_expanded(_aria(state), list_visible=bool(state.get("list_visible"))):
        return
    if not state.get("found"):
        _refuse(f"{label} list is not on the rail — scrape refused until it is expanded")
    clicked = await page.evaluate(_DISCLOSURE_JS, {"label": label, "click": True})
    if not (isinstance(clicked, dict) and clicked.get("clicked")):
        _refuse(f"{label} list is collapsed and has no toggle — scrape refused")
    await page.wait_for_timeout(400)
    opened = await _read_disclosure(page, label)
    aria = _aria(opened)
    visible = bool(opened.get("list_visible"))
    if disclosure_is_expanded(aria, list_visible=visible):
        return
    _refuse(
        f"{label} list is still collapsed after open "
        f"(aria-expanded={aria!r}, list_visible={visible}) — scrape refused"
    )


async def expand_context_frame(page: Page) -> bool:
    """Confirm the Context rail and the Skills list are expanded.

    Reads each disclosure before clicking. A collapsed list is opened
    once. An already-open list is not clicked, because the control
    toggles shut. Returns true only when both are open. Raises
    ``ChatContextSkillsError`` when either list is still closed.
    """
    await _require_disclosure_open(page, "Context")
    await _require_disclosure_open(page, "Skills")
    return True
