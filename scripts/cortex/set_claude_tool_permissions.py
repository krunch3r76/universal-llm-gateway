#!/usr/bin/env python3
"""Repair claude.ai Customize radios for the toys and ulg-code connectors.

Callers are ``claude-ai-sync-jupiter set-tool-permissions`` and
``refresh-connector``. ``toys`` on ``/mcp/life`` gets the Other-tools blanket
set to Always allow. ``ulg-code`` on ``/mcp/code`` gets the ops allowlist in
``claude_code_tool_permissions``: Manage Services, Observability, and Team
Dispatch stay Always allow, and every other tool radio is Blocked. The script
reloads and checks the saved control before it prints a status.
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
from claude_code_tool_permissions import apply_code_ops, resolve_policy  # noqa: E402
from claude_settings_page import pick_claude_settings_page  # noqa: E402
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
    """Navigate a usable Claude tab to Settings → Customize → Connectors."""
    page = await pick_claude_settings_page(page)
    await page.bring_to_front()

    if "claude.ai" not in page.url:
        await page.goto("https://claude.ai/new", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

    await _ensure_settings_open(page)
    if (
        "customize/connectors" not in page.url
        and "customize-connectors" not in page.url
    ):
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
        # The Yours list is not a table. The connector name is its own element,
        # and the MCP URL is not shown until the detail opens.
        if needle.startswith("http"):
            continue
        label = page.get_by_text(needle, exact=True)
        if await label.count() and await label.first.is_visible():
            return label.first
    return None


async def _open_connector_detail(
    page: Page, connector_name: str, mcp_url: str
) -> Page:
    """Open and validate the requested connector detail page."""
    host = _host(mcp_url)
    body = await page.locator("body").inner_text()
    if connector_name in body and mcp_url in body:
        return page
    # Discover hides custom connectors. Yours is the list that names them.
    if connector_name not in body:
        yours = page.get_by_text("Yours", exact=True)
        if await yours.count() and await yours.first.is_visible():
            await yours.first.click(force=True)
            await page.wait_for_timeout(1500)

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
    policy: str | None = None,
) -> str:
    """Apply the connector tool policy and return changed or already_set.

    ``policy`` None selects from the URL and connector name. Life repair
    checks the Other-tools blanket and returns one status word. Code-ops
    repair returns a second line, ``code-ops allow=<n> blocked=<n>``. The
    Jupiter page is reloaded on the connector detail before the status returns.
    """
    playwright, _browser, _context, page = await connect_cdp(cdp_url)
    timeout_ms = int(timeout_s * 1000)
    page.set_default_timeout(timeout_ms)
    try:
        selected = resolve_policy(mcp_url, connector_name, policy)
        page = await _open_connectors_panel(page)
        page = await _open_connector_detail(page, connector_name, mcp_url)

        async def reopen() -> Page:
            nonlocal page
            page = await _open_connectors_panel(page)
            page = await _open_connector_detail(page, connector_name, mcp_url)
            return page

        if selected == "code-ops":
            return await apply_code_ops(page, reopen=reopen)
        result = await _set_permission_group(page)
        await _verify_persisted(page)
        return result
    finally:
        await playwright.stop()


def main() -> int:
    """Parse CLI arguments, run the permission repair, and print its status.

    Stdout is the status word the wrapper quotes. Code-ops adds a second line
    with the allow and blocked counts after the reload check succeeds.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cdp-url",
        default=os.environ.get("BROWSER_CDP_URL", DEFAULT_CDP_URL),
    )
    parser.add_argument("--mcp-url", default=DEFAULT_MCP_URL)
    parser.add_argument("--connector-name", default=DEFAULT_CONNECTOR_NAME)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument(
        "--policy",
        choices=("life-blanket", "code-ops"),
        default=None,
        help="Override URL-based policy. life-blanket is toys; "
        "code-ops is the ulg-code allowlist.",
    )
    args = parser.parse_args()

    try:
        result = asyncio.run(
            set_tool_permissions(
                cdp_url=args.cdp_url,
                mcp_url=args.mcp_url,
                connector_name=args.connector_name,
                timeout_s=args.timeout,
                policy=args.policy,
            )
        )
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
