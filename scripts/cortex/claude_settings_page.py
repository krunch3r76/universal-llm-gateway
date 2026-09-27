"""Pick a claude.ai tab that can open Settings → Connectors."""

from __future__ import annotations

from playwright.async_api import Page

# Skill detail hides the account menu (visibility:hidden). Claude Code opens
# a Code-specific settings hash that does not render the connectors list.
_SKIP_PREFIXES = (
    "https://claude.ai/customize/skills",
    "https://claude.ai/code/",
)


async def pick_claude_settings_page(page: Page) -> Page:
    """Return a claude.ai page whose account menu can open the connectors list.

    Falls back to any tab with a visible account menu, then to a fresh
    ``/new`` tab when every existing claude.ai tab hides that menu.
    """
    fallback = None
    for tab in page.context.pages:
        url = tab.url or ""
        if "claude.ai" not in url:
            continue
        menu = tab.locator('[data-testid="user-menu-button"]')
        visible = bool(await menu.count() and await menu.first.is_visible())
        if not visible:
            continue
        if any(url.startswith(prefix) for prefix in _SKIP_PREFIXES):
            if fallback is None:
                fallback = tab
            continue
        return tab
    if fallback is not None:
        return fallback
    fresh = await page.context.new_page()
    await fresh.goto("https://claude.ai/new", wait_until="domcontentloaded")
    await fresh.wait_for_timeout(2000)
    return fresh
