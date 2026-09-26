"""Open the Cowork Context section before a Skills scrape.

``scrape_loaded_skills`` calls ``expand_context_frame``. On the live rail
the Skills rows are the grid under ``[data-section-header]`` labeled
Context (``role=button``, ``aria-expanded``). That header is the toggle.
The word Skills is a label inside the open grid, not a second disclosure.
A click on an already-open header closes it, so this module reads
``aria-expanded`` first and clicks only when the section is closed.
"""

from __future__ import annotations

from typing import Any

from playwright.async_api import Page

_DISCLOSURE_JS = """
(spec) => {
  const label = spec.label;
  const doClick = !!spec.click;
  const headers = Array.from(document.querySelectorAll('[data-section-header][role="button"]'));
  const header = headers.find((el) =>
    (el.innerText || '').trim().split('\\n')[0].trim() === label
  );
  if (!header) {
    return { found: false, aria_expanded: null, list_visible: false, clicked: false };
  }
  if (doClick) {
    header.click();
    return { found: true, aria_expanded: null, list_visible: false, clicked: true };
  }
  const panel = header.nextElementSibling;
  const style = panel ? getComputedStyle(panel) : null;
  const rows = style ? (style.gridTemplateRows || '') : '';
  const collapsed = !panel || rows === '0px' || rows === '0fr' || rows.startsWith('0px');
  const height = panel ? panel.getBoundingClientRect().height : 0;
  return {
    found: true,
    aria_expanded: header.getAttribute('aria-expanded'),
    list_visible: !collapsed && height > 0,
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
    """Confirm the Context section header is expanded before the scrape.

    The Skills rows live in the grid under that header. There is no
    separate Skills toggle. A collapsed header is opened once. An
    already-open header is not clicked, because the control toggles
    shut. Returns true when the section is open. Raises
    ``ChatContextSkillsError`` when the header stays closed.
    """
    await _require_disclosure_open(page, "Context")
    return True
