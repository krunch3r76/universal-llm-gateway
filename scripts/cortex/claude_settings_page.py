"""Pick a claude.ai tab that can open Customize → Connectors.

The permission and restore scripts call this before they touch a connector.
The Yours list lives on ``/new#customize/connectors`` and stays selected even
when the account menu is hidden by the settings surface.
"""

from __future__ import annotations

from playwright.async_api import Page

# Skill detail hides the account menu (visibility:hidden). Claude Code opens
# a Code-specific settings hash that does not render the connectors list.
_SKIP_PREFIXES = (
    "https://claude.ai/customize/skills",
    "https://claude.ai/code/",
)


async def pick_claude_settings_page(page: Page) -> Page:
    """Return a claude.ai page that can show Customize → Connectors.

    ``/new#customize/connectors`` wins even when the account menu is hidden.
    Otherwise use a tab with a visible account menu, then a fresh ``/new`` tab.
    Claude Code's settings hash is not the connector list.
    """
    # The live connectors list is this hash. The account menu is hidden while
    # that settings surface is open, so a menu-only scan skips the right tab.
    for tab in page.context.pages:
        url = tab.url or ""
        if url.startswith("https://claude.ai/new#customize/connectors"):
            return tab

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
