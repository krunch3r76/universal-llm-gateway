#!/usr/bin/env python3
"""Repair claude.ai Customize blanket radios for toys and ulg-code connectors.

Callers are ``claude-ai-sync-jupiter set-tool-permissions`` and
``refresh-connector``. ``toys`` on ``/mcp/life`` sets **Other tools** → Always
allow. ``ulg-code`` on ``/mcp/code`` sets **Read-only tools** and **Other
tools** → Always allow. Reload verifies persistence before printing status.
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
from claude_code_tool_permissions import (  # noqa: E402
    apply_code_blanket,
    apply_life_blanket,
    resolve_policy,
)
from claude_settings_page import pick_claude_settings_page  # noqa: E402
from playwright.async_api import Page  # noqa: E402

DEFAULT_MCP_URL = "https://mcp.k-1.me/mcp/life"
DEFAULT_CONNECTOR_NAME = "toys"


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


async def set_tool_permissions(
    *,
    cdp_url: str,
    mcp_url: str,
    connector_name: str,
    timeout_s: float,
    policy: str | None = None,
) -> str:
    """Apply the connector blanket policy and return status plus group summary."""
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

        if selected == "code-blanket":
            return await apply_code_blanket(page, reopen=reopen)
        return await apply_life_blanket(page, reopen=reopen)
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
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument(
        "--policy",
        choices=("life-blanket", "code-blanket", "code-ops"),
        default=None,
        help="Override URL-based policy. code-ops is a legacy alias for "
        "code-blanket.",
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
