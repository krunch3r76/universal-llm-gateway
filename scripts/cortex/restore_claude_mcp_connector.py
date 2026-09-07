#!/usr/bin/env python3
"""Restore / rename claude.ai MCP connector via Playwright + CDP (Jupiter).

Opens Customize → Connectors, ensures the life-surface connector exists under
the desired display name (default ``toys``), Connect/Reconnect + OAuth Approve
on mcp.k-1.me when needed.

If the life URL is already connected under a different display name (e.g.
``vortex``), removes that row and re-adds as ``--connector-name`` (claude.ai
has no in-UI rename — only Remove).

Default URL: https://mcp.k-1.me/mcp/life

Playbook: agent_skill:claude-ai-mcp-connect → .cursor/skills/claude-ai-mcp-connect/SKILL.md

Operator restore (tools dead / connection expired): prefer wrapper
``refresh-connector`` (this script with ``--force-reconnect`` + permission repair).
Plain restore may exit ``already_connected`` while chat tools remain stale.

Usage (CDP host / Jupiter):
  BROWSER_CDP_URL=http://127.0.0.1:9222 python scripts/cortex/restore_claude_mcp_connector.py \\
    --mcp-url 'https://mcp.k-1.me/mcp/life' --connector-name toys --force-reconnect

From Cursor / remote seat:
  scripts/cortex/claude-ai-sync-jupiter refresh-connector \\
    --mcp-url 'https://mcp.k-1.me/mcp/life' --connector-name toys
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

from restore_claude_mcp_connector_mutate import (  # noqa: E402
    add_custom_connector,
    remove_named_connector,
)
DEFAULT_MCP_URL = "https://mcp.k-1.me/mcp/life"
DEFAULT_CONNECTOR_NAME = "toys"
_LEGACY_NAMES = ("vortex",)
_OAUTH_AUTHORIZE = re.compile(r"https://mcp\.k-1\.me/oauth/authorize")
_CONNECTION_ISSUE = re.compile(r"connection (issue|expired)", re.I)


def _host(mcp_url: str) -> str:
    return mcp_url.replace("https://", "").replace("http://", "").split("/")[0]


async def _ensure_settings_open(page: Page) -> None:
    connectors_btn = page.locator('button:has-text("Connectors")')
    if await connectors_btn.count() and await connectors_btn.first.is_visible():
        return

    for sel in (
        '[data-testid="user-menu-button"]',
        'button:has-text("Kaywan")',
    ):
        menu = page.locator(sel)
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
    if await page.locator("tr").filter(
        has_text=re.compile(r"vortex|toys|mcp\.k-1", re.I)
    ).count():
        return True
    add = page.get_by_role("button", name=re.compile(r"^add\b", re.I))
    return bool(await add.count() and await add.first.is_visible())


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
        await connectors_btn.first.click(force=True)
        await page.wait_for_timeout(2000)

    if await _connectors_panel_ready(page):
        return page

    # Retry: settings may have failed silently (hash alone ≠ Settings modal).
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
            "Connectors panel not open — in Jupiter Chrome: open Settings → "
            "Customize → Connectors, then re-run."
        )
    return page


async def _row_matching(page: Page, *needles: str):
    for needle in needles:
        row = page.locator("tr").filter(has_text=re.compile(re.escape(needle), re.I))
        if await row.count():
            return row.first
    return None


async def _back_to_list(page: Page) -> Page:
    back = page.get_by_role("button", name=re.compile(r"connectors", re.I))
    if await back.count() and await back.first.is_visible():
        await back.first.click(force=True)
        await page.wait_for_timeout(1500)
    return page


async def _open_connector_detail(page: Page, connector_name: str, mcp_url: str) -> Page:
    host = _host(mcp_url)
    if await page.get_by_text(mcp_url, exact=False).count() or await page.get_by_text(
        host, exact=False
    ).count():
        # Already on a detail that shows the URL (or list with URL visible).
        disconnect = page.get_by_role("button", name=re.compile(r"^disconnect$", re.I))
        if await disconnect.count():
            return page

    row = await _row_matching(page, connector_name, mcp_url, host, *_LEGACY_NAMES)
    if row is None:
        page = await _back_to_list(page)
        row = await _row_matching(page, connector_name, mcp_url, host, *_LEGACY_NAMES)
    if row is None:
        raise RuntimeError(
            f"Connector row not found for {connector_name!r} / {mcp_url}"
        )
    await row.click(force=True)
    await page.wait_for_timeout(2000)
    return page


async def _approve_oauth(page: Page, context, timeout_ms: int) -> Page:
    await page.bring_to_front()
    approve = page.locator('button[type="submit"]').filter(
        has_text=re.compile(r"approve", re.I)
    )
    if not await approve.count():
        approve = page.get_by_role("button", name=re.compile(r"approve", re.I))
    if not await approve.count():
        raise RuntimeError("OAuth Approve button not found")
    await approve.first.click()
    await page.wait_for_url(re.compile(r"https://claude\.ai/"), timeout=timeout_ms)
    await page.wait_for_timeout(4000)
    body = await page.locator("body").inner_text()
    if _CONNECTION_ISSUE.search(body):
        raise RuntimeError("claude.ai still shows connection issue after OAuth")
    return page


async def _disconnect_if_connected(page: Page) -> bool:
    """Click Disconnect when the detail shows a live session (tools/list refresh)."""
    disconnect = page.get_by_role("button", name=re.compile(r"^disconnect$", re.I))
    if not await disconnect.count() or not await disconnect.first.is_visible():
        return False
    await disconnect.first.click(force=True)
    await page.wait_for_timeout(1500)
    confirm = page.get_by_role(
        "button", name=re.compile(r"^(disconnect|confirm|yes)$", re.I)
    )
    if await confirm.count() and await confirm.first.is_visible():
        await confirm.first.click(force=True)
        await page.wait_for_timeout(1500)
    return True


async def _click_connect_and_oauth(
    page: Page,
    context,
    timeout_ms: int,
    *,
    force_reconnect: bool = False,
) -> str:
    if force_reconnect:
        await _disconnect_if_connected(page)
        await page.wait_for_timeout(1000)

    connect_btn = page.get_by_role(
        "button", name=re.compile(r"^(connect|reconnect)$", re.I)
    )
    if not await connect_btn.count():
        body = await page.locator("body").inner_text()
        mcp_visible = "mcp.k-1.me" in body
        if not force_reconnect and not _CONNECTION_ISSUE.search(body) and mcp_visible:
            return "already_connected"
        raise RuntimeError("Connect/Reconnect button not found")

    oauth_page = page
    try:
        async with page.expect_navigation(url=_OAUTH_AUTHORIZE, timeout=timeout_ms):
            await connect_btn.first.click(force=True)
        oauth_page = page
    except PlaywrightTimeout:
        await connect_btn.first.click(force=True)
        await page.wait_for_timeout(2000)
        for tab in context.pages:
            if "mcp.k-1.me/oauth/authorize" in tab.url:
                oauth_page = tab
                break
        else:
            await page.wait_for_url(_OAUTH_AUTHORIZE, timeout=timeout_ms)
            oauth_page = page

    await _approve_oauth(oauth_page, context, timeout_ms)
    return "restored"


async def restore_connector(
    *,
    cdp_url: str,
    mcp_url: str,
    connector_name: str,
    timeout_s: float,
    force_reconnect: bool = False,
) -> str:
    pw, _browser, context, page = await connect_cdp(cdp_url)
    timeout_ms = int(timeout_s * 1000)

    async def _connect(
        p: Page, ctx=context, t_ms: int = timeout_ms
    ) -> str:
        return await _click_connect_and_oauth(
            p, ctx, t_ms, force_reconnect=force_reconnect
        )

    try:
        page = await _open_connectors_panel(page)
        desired = await _row_matching(page, connector_name)
        legacy_found: str | None = None
        for legacy_name in _LEGACY_NAMES:
            if legacy_name.lower() == connector_name.lower():
                continue
            if await _row_matching(page, legacy_name) is not None:
                legacy_found = legacy_name
                break

        if desired is None and legacy_found is not None:
            # Display rename: no in-UI rename — remove legacy, re-add as desired.
            page = await remove_named_connector(
                page,
                legacy_found,
                back_to_list=_back_to_list,
                row_matching=_row_matching,
                open_connectors_panel=_open_connectors_panel,
            )
            return await add_custom_connector(
                page,
                connector_name=connector_name,
                mcp_url=mcp_url,
                context=context,
                timeout_ms=timeout_ms,
                back_to_list=_back_to_list,
                approve_oauth=_approve_oauth,
                click_connect_and_oauth=_connect,
            )

        if desired is None and legacy_found is None:
            url_row = await _row_matching(page, mcp_url, _host(mcp_url))
            if url_row is None:
                return await add_custom_connector(
                    page,
                    connector_name=connector_name,
                    mcp_url=mcp_url,
                    context=context,
                    timeout_ms=timeout_ms,
                    back_to_list=_back_to_list,
                    approve_oauth=_approve_oauth,
                    click_connect_and_oauth=_connect,
                )

        page = await _open_connector_detail(page, connector_name, mcp_url)
        if not await page.get_by_text(mcp_url, exact=False).count():
            raise RuntimeError(f"Connector detail for {mcp_url} not visible")
        return await _connect(page)
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
    parser.add_argument(
        "--force-reconnect",
        action="store_true",
        help="Disconnect then Connect+OAuth even when already Connected "
        "(forces tools/list refresh after MCP schema changes).",
    )
    args = parser.parse_args()

    try:
        result = asyncio.run(
            restore_connector(
                cdp_url=args.cdp_url,
                mcp_url=args.mcp_url,
                connector_name=args.connector_name,
                timeout_s=args.timeout,
                force_reconnect=args.force_reconnect,
            )
        )
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
