#!/usr/bin/env python3
"""Set Claude's life-connector tool policy through the authenticated Jupiter tab.

The script targets only the named ``toys`` connector and its ``Other tools``
permission group. It reads the current policy, changes it only when necessary,
then reloads the page to verify that the setting persisted and tools remain
available.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))

from claude_bundles.skills_ui_panel import DEFAULT_CDP_URL, connect_cdp  # noqa: E402
from playwright.async_api import Page  # noqa: E402

DEFAULT_MCP_URL = "https://mcp.k-1.me/mcp/life"
DEFAULT_CONNECTOR_NAME = "toys"
PERMISSION_GROUP = "Other tools"
ALLOWED_POLICY = "Always allow"
NO_TOOLS_MESSAGE = "This connector has no tools available."


def _host(mcp_url: str) -> str:
    """Return the hostname used to identify a connector row."""
    return mcp_url.replace("https://", "").replace("http://", "").split("/")[0]


async def _ensure_settings_open(page: Page) -> None:
    """Open the authenticated Settings modal when the connectors panel is absent."""
    connectors_btn = page.locator('button:has-text("Connectors")')
    if await connectors_btn.count() and await connectors_btn.first.is_visible():
        return

    for selector in (
        '[data-testid="user-menu-button"]',
        'button:has-text("Kaywan")',
    ):
        menu = page.locator(selector)
        if await menu.count() and await menu.first.is_visible():
            await menu.first.click(force=True)
            await page.wait_for_timeout(1000)
            break

    settings = page.get_by_role("menuitem", name=re.compile(r"settings", re.I))
    if not await settings.count():
        settings = page.locator("text=Settings")
    if await settings.count() and await settings.first.is_visible():
        await settings.first.click(force=True)
        await page.wait_for_timeout(2000)


async def _connectors_panel_ready(page: Page) -> bool:
    """Return whether the settings page has rendered its connectors controls."""
    if await page.locator("tr").filter(
        has_text=re.compile(r"vortex|toys|mcp\.k-1", re.I)
    ).count():
        return True
    add = page.get_by_role("button", name=re.compile(r"^add\b", re.I))
    return bool(await add.count() and await add.first.is_visible())


async def _open_connectors_panel(page: Page) -> Page:
    """Navigate the existing Claude tab to Settings → Customize → Connectors."""
    for tab in page.context.pages:
        if "claude.ai" in tab.url:
            page = tab
            break
    await page.bring_to_front()

    if "claude.ai" not in page.url:
        await page.goto("https://claude.ai/new", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

    await _ensure_settings_open(page)
    if "customize-connectors" not in page.url:
        await page.evaluate(
            "() => { window.location.hash = 'settings/customize-connectors'; "
            "window.dispatchEvent(new HashChangeEvent('hashchange')); }"
        )
        await page.wait_for_timeout(2000)

    connectors_btn = page.locator('button:has-text("Connectors")')
    if await connectors_btn.count() and await connectors_btn.first.is_visible():
        await connectors_btn.first.click(force=True)
        await page.wait_for_timeout(2000)
    if await _connectors_panel_ready(page):
        return page

    await _ensure_settings_open(page)
    await page.evaluate(
        "() => { window.location.hash = 'settings/customize-connectors'; "
        "window.dispatchEvent(new HashChangeEvent('hashchange')); }"
    )
    await page.wait_for_timeout(2000)
    connectors_btn = page.locator('button:has-text("Connectors")')
    if await connectors_btn.count() and await connectors_btn.first.is_visible():
        await connectors_btn.first.click(force=True)
        await page.wait_for_timeout(2000)

    if not await _connectors_panel_ready(page):
        raise RuntimeError(
            "Connectors panel not open — open Settings → Customize → "
            "Connectors in Jupiter Chrome and re-run."
        )
    return page


async def _row_matching(page: Page, *needles: str):
    """Find the first connector row containing one of the supplied needles."""
    for needle in needles:
        row = page.locator("tr").filter(has_text=re.compile(re.escape(needle), re.I))
        if await row.count():
            return row.first
    return None


async def _open_connector_detail(
    page: Page, connector_name: str, mcp_url: str
) -> Page:
    """Open and validate the requested connector detail page."""
    host = _host(mcp_url)
    body = await page.locator("body").inner_text()
    if connector_name in body and mcp_url in body:
        return page

    row = await _row_matching(page, connector_name, mcp_url, host)
    if row is None:
        raise RuntimeError(
            f"Connector row not found for {connector_name!r} / {mcp_url}"
        )
    await row.click(force=True)
    await page.wait_for_timeout(2000)
    body = await page.locator("body").inner_text()
    if connector_name not in body or mcp_url not in body:
        raise RuntimeError(
            f"Connector detail does not show {connector_name!r} and {mcp_url}"
        )
    return page


async def _set_permission_group(page: Page) -> str:
    """Set the fixed Other tools group and return ``changed`` or ``already_set``."""
    group_heading = page.get_by_text(PERMISSION_GROUP, exact=True)
    if await group_heading.count() != 1:
        raise RuntimeError(
            f"Expected exactly one {PERMISSION_GROUP!r} permission group"
        )

    group_row = group_heading.locator("xpath=../..")
    policy_button = group_row.get_by_role(
        "button", name="Blanket permission for group"
    )
    if await policy_button.count() != 1:
        raise RuntimeError("Other tools blanket permission control not found")

    current = (await policy_button.inner_text()).strip()
    if ALLOWED_POLICY in current:
        return "already_set"

    await policy_button.click(force=True)
    await page.wait_for_timeout(300)
    menu = page.locator('[role="menu"][data-open]')
    if await menu.count() != 1:
        raise RuntimeError("Other tools permission menu did not open")

    allowed = menu.get_by_role("menuitemradio").filter(has_text=ALLOWED_POLICY)
    if await allowed.count() != 1:
        raise RuntimeError("Always allow option is not uniquely identifiable")
    await allowed.first.click(force=True)
    await page.wait_for_timeout(700)
    return "changed"


async def _verify_persisted(page: Page) -> None:
    """Reload the page and verify the permission and tools-list indicators."""
    await page.reload(wait_until="domcontentloaded")
    await page.get_by_text(PERMISSION_GROUP, exact=True).wait_for(
        state="visible"
    )
    group_heading = page.get_by_text(PERMISSION_GROUP, exact=True)
    group_row = group_heading.locator("xpath=../..")
    policy_button = group_row.get_by_role(
        "button", name="Blanket permission for group"
    )
    current = (await policy_button.inner_text()).strip()
    if ALLOWED_POLICY not in current:
        raise RuntimeError(f"Permission did not persist: {current!r}")
    if NO_TOOLS_MESSAGE in await page.locator("body").inner_text():
        raise RuntimeError("Claude still reports that the connector has no tools")


async def set_tool_permissions(
    *,
    cdp_url: str,
    mcp_url: str,
    connector_name: str,
    timeout_s: float,
) -> str:
    """Set and reload-verify the toys Other tools policy through Jupiter CDP."""
    playwright, _browser, _context, page = await connect_cdp(cdp_url)
    timeout_ms = int(timeout_s * 1000)
    page.set_default_timeout(timeout_ms)
    try:
        page = await _open_connectors_panel(page)
        page = await _open_connector_detail(page, connector_name, mcp_url)
        result = await _set_permission_group(page)
        await _verify_persisted(page)
        return result
    finally:
        await playwright.stop()


def main() -> int:
    """Parse CLI arguments, run the permission repair, and print its status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cdp-url",
        default=os.environ.get("BROWSER_CDP_URL", DEFAULT_CDP_URL),
    )
    parser.add_argument("--mcp-url", default=DEFAULT_MCP_URL)
    parser.add_argument("--connector-name", default=DEFAULT_CONNECTOR_NAME)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    try:
        result = asyncio.run(
            set_tool_permissions(
                cdp_url=args.cdp_url,
                mcp_url=args.mcp_url,
                connector_name=args.connector_name,
                timeout_s=args.timeout,
            )
        )
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
