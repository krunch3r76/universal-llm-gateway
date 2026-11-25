"""Pick a claude.ai tab and wait for Plugins → Connectors (Yours) to load.

Custom MCP rows live on ``/new#settings/plugins/yours`` (Connectors sub-tab).
Legacy ``/customize/connectors`` now redirects to the public plugin marketplace.
"""

from __future__ import annotations

import re
import time

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
        if url.startswith("https://claude.ai/new#settings/plugins/yours"):
            return tab
        if url.startswith("https://claude.ai/new#customize/connectors"):
            return tab

    # Reuse any logged-in claude.ai tab — a fresh /new tab often lacks cookies
    # when CDP was attached to plugins or another surface (connector restore).
    generic_claude: Page | None = None
    fallback = None
    for tab in page.context.pages:
        url = tab.url or ""
        if "claude.ai" not in url:
            continue
        if generic_claude is None and not any(
            url.startswith(prefix) for prefix in _SKIP_PREFIXES
        ):
            generic_claude = tab
        menu = tab.locator('[data-testid="user-menu-button"]')
        visible = bool(await menu.count() and await menu.first.is_visible())
        if not visible:
            continue
        if any(url.startswith(prefix) for prefix in _SKIP_PREFIXES):
            if fallback is None:
                fallback = tab
            continue
        return tab
    if generic_claude is not None:
        return generic_claude
    if fallback is not None:
        return fallback
    fresh = await page.context.new_page()
    await fresh.goto(
        "https://claude.ai/new#settings/plugins/yours",
        wait_until="domcontentloaded",
    )
    await fresh.wait_for_timeout(2500)
    return fresh


_LOADED_ROW = re.compile(
    r"vortex|toys|ulg-code|custom connector|mcp\.(k-1|askapharmd)", re.I
)
_PLUGINS_LOADING = re.compile(r"(?i)your plugins.{0,40}loading|loading.{0,40}your plugins")


async def wait_plugins_connectors_loaded(
    page: Page, *, max_wait_s: float = 90.0, poll_ms: int = 2500
) -> None:
    """Poll until Plugins → Connectors finishes loading (often tens of seconds)."""
    if "settings/plugins" not in page.url:
        return

    connectors = page.get_by_text("Connectors", exact=True)
    if await connectors.count() and await connectors.first.is_visible():
        await connectors.first.click(force=True)

    deadline = time.monotonic() + max_wait_s
    while time.monotonic() < deadline:
        body = await page.locator("body").inner_text()
        if _LOADED_ROW.search(body):
            return
        add = page.get_by_role("button", name=re.compile(r"^add\b", re.I))
        if await add.count() and await add.first.is_visible():
            return
        if "Remote MCP" in body or "MCP server URL" in body:
            return
        if "modal=add-custom-connector" in page.url:
            return
        # Loaded empty state: no spinner copy next to Your plugins.
        if "Your plugins" in body and not _PLUGINS_LOADING.search(body):
            if await page.get_by_text(re.compile(r"add custom connector", re.I)).count():
                return
            if await page.locator("tr").filter(has_text=_LOADED_ROW).count():
                return
            # Settled list with zero rows still counts as loaded (Add may appear later).
            if not re.search(r"(?i)\bloading\b", body):
                return
        await page.wait_for_timeout(poll_ms)

    raise RuntimeError(
        "Plugins → Connectors did not finish loading within "
        f"{max_wait_s:.0f}s — wait for the list in Chrome, then re-run."
    )
