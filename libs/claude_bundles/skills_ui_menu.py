"""claude.ai Skills Add → Upload menu open/click helpers."""

from __future__ import annotations

import re

from playwright.async_api import Locator, Page

_UPLOAD_MENU = re.compile(r"upload a skill", re.I)


async def add_menu_expanded(add_btn: Locator) -> bool:
    try:
        expanded = await add_btn.get_attribute("aria-expanded", timeout=2_000)
    except Exception:
        return False
    return (expanded or "").lower() == "true"


async def stability_guarded_add_click(add_btn: Locator) -> None:
    """Open Add menu; skip click when already expanded (re-click toggles shut)."""
    await add_btn.scroll_into_view_if_needed()
    await add_btn.wait_for(state="visible", timeout=3_000)
    if await add_menu_expanded(add_btn):
        return
    await add_btn.click(timeout=3_000)


async def js_click_upload_menuitem(page: Page, add_btn: Locator) -> dict:
    handle = await add_btn.element_handle()
    return await page.evaluate(
        """(btn) => {
          const el = btn || document.querySelector('button[aria-label="Add skill"]')
            || document.querySelector('button[aria-haspopup="menu"]');
          const menuId = el && el.getAttribute('aria-controls');
          const scoped = menuId && document.getElementById(menuId);
          const pick = (root) => {
            const items = [...(root || document).querySelectorAll('[role=menuitem]')];
            const target = items.find(e => /upload a skill/i.test(e.innerText || ''));
            return {target, n: items.length};
          };
          let hit = pick(scoped);
          if (!hit.target) hit = pick(document);
          if (!hit.target) return {ok: false, n: hit.n};
          hit.target.click();
          return {ok: true, n: hit.n};
        }""",
        handle,
    )


async def wait_upload_menuitem(page: Page, *, timeout_ms: int = 4_000) -> Locator | None:
    """Poll until Upload menuitem is present (menu portal can lag Add expand)."""
    deadline = timeout_ms
    step = 200
    while deadline > 0:
        upload_item = page.get_by_role("menuitem", name=_UPLOAD_MENU)
        if await upload_item.count():
            return upload_item.first
        upload_item = page.locator("[role='menuitem']").filter(has_text=_UPLOAD_MENU)
        if await upload_item.count():
            return upload_item.first
        await page.wait_for_timeout(step)
        deadline -= step
    return None
