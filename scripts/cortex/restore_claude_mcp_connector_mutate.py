"""Remove / re-add helpers for claude.ai MCP connector display rename.

claude.ai has no in-UI rename — display rename = Remove + Add custom connector.
Used by ``restore_claude_mcp_connector.py`` (canonical playbook path).
"""

from __future__ import annotations

import re

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

_OAUTH_AUTHORIZE = re.compile(r"https://mcp\.k-1\.me/oauth/authorize")


async def remove_named_connector(
    page: Page,
    name: str,
    *,
    back_to_list,
    row_matching,
    open_connectors_panel,
) -> Page:
    page = await back_to_list(page)
    row = await row_matching(page, name)
    if row is None:
        return page
    await row.click(force=True)
    await page.wait_for_timeout(1500)
    more = page.get_by_role(
        "button", name=re.compile(rf"More options for {re.escape(name)}", re.I)
    )
    if not await more.count():
        raise RuntimeError(f"More options for {name!r} not found")
    await more.first.click(force=True)
    await page.wait_for_timeout(800)
    remove = page.get_by_role("menuitem", name=re.compile(r"^remove$", re.I))
    if not await remove.count():
        raise RuntimeError("Remove menuitem not found")
    await remove.first.click(force=True)
    await page.wait_for_timeout(1000)
    confirm = page.get_by_role(
        "button", name=re.compile(r"^(remove|delete|confirm)$", re.I)
    )
    if await confirm.count() and await confirm.first.is_visible():
        await confirm.first.click(force=True)
        await page.wait_for_timeout(1500)
    return await open_connectors_panel(page)


async def add_custom_connector(
    page: Page,
    *,
    connector_name: str,
    mcp_url: str,
    context,
    timeout_ms: int,
    back_to_list,
    approve_oauth,
    click_connect_and_oauth,
) -> str:
    page = await back_to_list(page)
    add = page.get_by_role("button", name=re.compile(r"^add\b", re.I))
    if not await add.count():
        raise RuntimeError("Add button not found on Connectors list")
    await add.first.click(force=True)
    await page.wait_for_timeout(800)
    custom = page.get_by_role(
        "menuitem", name=re.compile(r"add custom connector", re.I)
    )
    if not await custom.count():
        custom = page.get_by_text(re.compile(r"add custom connector", re.I))
    if not await custom.count():
        raise RuntimeError("Add custom connector not found")
    await custom.first.click(force=True)
    await page.wait_for_timeout(1200)

    name_input = page.locator('input[placeholder="Name"]')
    if not await name_input.count():
        name_input = page.get_by_placeholder(re.compile(r"^name$", re.I))
    url_input = page.locator('input[placeholder*="Remote MCP" i]')
    if not await url_input.count():
        url_input = page.get_by_placeholder(re.compile(r"mcp.*url|url", re.I))
    if not await name_input.count() or not await url_input.count():
        raise RuntimeError("Add-connector name/URL inputs not found")
    await name_input.first.fill(connector_name)
    await url_input.first.fill(mcp_url)
    await page.wait_for_timeout(400)

    # Form Add is typically the last Add-named button (list Add opens the menu).
    submit = page.get_by_role("button", name=re.compile(r"^add\b", re.I)).last
    if not await page.get_by_role("button", name=re.compile(r"^add\b", re.I)).count():
        raise RuntimeError("Add submit button not found")

    try:
        async with page.expect_navigation(url=_OAUTH_AUTHORIZE, timeout=timeout_ms):
            await submit.click(force=True)
        oauth_page = page
    except PlaywrightTimeout:
        await submit.click(force=True)
        await page.wait_for_timeout(2000)
        oauth_page = page
        for tab in context.pages:
            if "mcp.k-1.me/oauth/authorize" in tab.url:
                oauth_page = tab
                break
        else:
            if await page.get_by_role(
                "button", name=re.compile(r"^(connect|reconnect)$", re.I)
            ).count():
                return await click_connect_and_oauth(page, context, timeout_ms)
            await page.wait_for_url(_OAUTH_AUTHORIZE, timeout=timeout_ms)
            oauth_page = page

    await approve_oauth(oauth_page, context, timeout_ms)
    return "renamed_readded"
