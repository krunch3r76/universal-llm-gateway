#!/usr/bin/env python3
"""Restore claude.ai MCP connector via Playwright + CDP (Jupiter Chrome session).

Opens Customize → Connectors, clicks Connect for the vortex MCP connector, approves
the OAuth consent page on mcp.k-1.me, and verifies claude.ai reports connected.

Usage (on CDP host, typically Jupiter):
  BROWSER_CDP_URL=http://127.0.0.1:9222 python scripts/cortex/restore_claude_mcp_connector.py

From Cursor / remote seat:
  scripts/cortex/claude-ai-sync-jupiter restore-connector
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
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout  # noqa: E402

DEFAULT_MCP_URL = "https://mcp.k-1.me/mcp"
DEFAULT_CONNECTOR_NAME = "vortex"
_OAUTH_AUTHORIZE = re.compile(r"https://mcp\.k-1\.me/oauth/authorize")
_CONNECTION_ISSUE = re.compile(r"connection (issue|expired)", re.I)


async def _ensure_settings_open(page: Page) -> None:
    connectors_btn = page.locator('button:has-text("Connectors")')
    if await connectors_btn.count() and await connectors_btn.first.is_visible():
        return

    for sel in (
        "button:has-text(\"Kaywan\")",
        "[data-testid=\"user-menu-button\"]",
    ):
        menu = page.locator(sel)
        if await menu.count() and await menu.first.is_visible():
            await menu.first.click()
            await page.wait_for_timeout(1000)
            break

    settings = page.locator("text=Settings")
    if await settings.count() and await settings.first.is_visible():
        await settings.first.click()
        await page.wait_for_timeout(2000)


async def _open_connectors_panel(page: Page) -> Page:
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
        await connectors_btn.first.click()
        await page.wait_for_timeout(2000)

    if not await page.locator("tr").filter(
        has_text=re.compile(r"vortex|mcp\.k-1", re.I)
    ).count():
        raise RuntimeError(
            "Connectors panel not open — in Jupiter Chrome: open Settings → "
            "Customize → Connectors, then re-run."
        )
    return page


async def _open_connector_detail(page: Page, connector_name: str, mcp_url: str) -> Page:
    host = mcp_url.replace("https://", "").replace("http://", "").split("/")[0]
    if await page.get_by_text(mcp_url, exact=False).count() or await page.get_by_text(
        host, exact=False
    ).count():
        return page

    row = page.locator("tr").filter(
        has_text=re.compile(re.escape(connector_name), re.I)
    )
    if not await row.count():
        row = page.locator("tr").filter(has_text=re.compile(re.escape(host), re.I))
    if not await row.count():
        back = page.get_by_role("button", name=re.compile(r"connectors", re.I))
        if await back.count() and await back.first.is_visible():
            await back.first.click()
            await page.wait_for_timeout(1500)
            row = page.locator("tr").filter(
                has_text=re.compile(re.escape(connector_name), re.I)
            )
    if not await row.count():
        raise RuntimeError(
            f"Connector row not found for {connector_name!r} / {mcp_url}"
        )
    await row.first.click()
    await page.wait_for_timeout(2000)
    return page


async def restore_connector(
    *,
    cdp_url: str,
    mcp_url: str,
    connector_name: str,
    timeout_s: float,
) -> str:
    pw, _browser, context, page = await connect_cdp(cdp_url)
    timeout_ms = int(timeout_s * 1000)
    try:
        page = await _open_connectors_panel(page)
        page = await _open_connector_detail(page, connector_name, mcp_url)

        if not await page.get_by_text(mcp_url, exact=False).count():
            raise RuntimeError(f"Connector detail for {mcp_url} not visible")

        connect_btn = page.get_by_role(
            "button", name=re.compile(r"^(connect|reconnect)$", re.I)
        )
        if not await connect_btn.count():
            body = await page.locator("body").inner_text()
            if not _CONNECTION_ISSUE.search(body) and mcp_url in body:
                return "already_connected"
            raise RuntimeError("Connect/Reconnect button not found")

        oauth_page = page
        try:
            async with page.expect_navigation(url=_OAUTH_AUTHORIZE, timeout=timeout_ms):
                await connect_btn.first.click()
            oauth_page = page
        except PlaywrightTimeout:
            await connect_btn.first.click()
            await page.wait_for_timeout(2000)
            for tab in context.pages:
                if "mcp.k-1.me/oauth/authorize" in tab.url:
                    oauth_page = tab
                    break
            else:
                await page.wait_for_url(_OAUTH_AUTHORIZE, timeout=timeout_ms)
                oauth_page = page

        await oauth_page.bring_to_front()
        approve = oauth_page.locator('button[type="submit"]').filter(
            has_text=re.compile(r"approve", re.I)
        )
        if not await approve.count():
            approve = oauth_page.get_by_role(
                "button", name=re.compile(r"approve", re.I)
            )
        if not await approve.count():
            raise RuntimeError("OAuth Approve button not found")
        await approve.first.click()

        await oauth_page.wait_for_url(
            re.compile(r"https://claude\.ai/"), timeout=timeout_ms
        )
        await oauth_page.wait_for_timeout(4000)

        body = await oauth_page.locator("body").inner_text()
        if _CONNECTION_ISSUE.search(body):
            raise RuntimeError("claude.ai still shows connection issue after OAuth")

        return "restored"
    finally:
        await pw.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cdp-url",
        default=os.environ.get("BROWSER_CDP_URL", DEFAULT_CDP_URL),
    )
    parser.add_argument("--mcp-url", default=DEFAULT_MCP_URL)
    parser.add_argument("--connector-name", default=DEFAULT_CONNECTOR_NAME)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()

    try:
        result = asyncio.run(
            restore_connector(
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
