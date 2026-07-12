#!/usr/bin/env python3
"""Repoint claude.ai vortex connector from bare /mcp to /mcp/life.

Opens Customize → Connectors, edits the vortex remote MCP URL, saves, then
runs the same OAuth reconnect path as restore_claude_mcp_connector.py.

Usage (Jupiter CDP):
  BROWSER_CDP_URL=http://127.0.0.1:9222 \\
    python scripts/cortex/repoint_claude_mcp_connector.py \\
    --from-url https://mcp.k-1.me/mcp \\
    --to-url https://mcp.k-1.me/mcp/life
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_CORTEX = Path(__file__).resolve().parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

from claude_bundles.skills_ui_panel import DEFAULT_CDP_URL, connect_cdp  # noqa: E402
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout  # noqa: E402

from restore_claude_mcp_connector import (  # noqa: E402
    DEFAULT_CONNECTOR_NAME,
    _CONNECTION_ISSUE,
    _OAUTH_AUTHORIZE,
    _open_connector_detail,
    _open_connectors_panel,
)

DEFAULT_FROM = "https://mcp.k-1.me/mcp"
DEFAULT_TO = "https://mcp.k-1.me/mcp/life"


async def _fill_url(page: Page, to_url: str) -> None:
    # Prefer labeled URL / MCP server fields; fall back to inputs containing mcp.k-1.me.
    candidates = [
        page.get_by_label(re.compile(r"(mcp|server|url|remote)", re.I)),
        page.locator('input[type="url"]'),
        page.locator('input[type="text"]'),
    ]
    target = None
    for loc in candidates:
        count = await loc.count()
        for i in range(count):
            el = loc.nth(i)
            if not await el.is_visible():
                continue
            val = await el.input_value()
            if "mcp.k-1.me" in val or "mcp" in (await el.get_attribute("name") or "").lower():
                target = el
                break
            placeholder = (await el.get_attribute("placeholder") or "").lower()
            if "mcp" in placeholder or "url" in placeholder:
                target = el
                break
        if target is not None:
            break
    if target is None:
        raise RuntimeError("Could not find MCP URL input on connector detail")
    await target.fill(to_url)
    await page.wait_for_timeout(500)


async def _save_if_present(page: Page) -> None:
    save = page.get_by_role("button", name=re.compile(r"^(save|update|done)$", re.I))
    if await save.count() and await save.first.is_visible():
        await save.first.click()
        await page.wait_for_timeout(2000)


async def _reconnect(page: Page, context, mcp_url: str, timeout_ms: int) -> str:
    if not await page.get_by_text(mcp_url, exact=False).count():
        raise RuntimeError(f"After edit, connector detail does not show {mcp_url}")

    connect_btn = page.get_by_role(
        "button", name=re.compile(r"^(connect|reconnect)$", re.I)
    )
    if not await connect_btn.count():
        body = await page.locator("body").inner_text()
        if not _CONNECTION_ISSUE.search(body) and mcp_url in body:
            return "url_updated_already_connected"
        raise RuntimeError("Connect/Reconnect button not found after URL edit")

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
        approve = oauth_page.get_by_role("button", name=re.compile(r"approve", re.I))
    if not await approve.count():
        raise RuntimeError("OAuth Approve button not found")
    await approve.first.click()
    await oauth_page.wait_for_url(re.compile(r"https://claude\.ai/"), timeout=timeout_ms)
    await oauth_page.wait_for_timeout(4000)
    body = await oauth_page.locator("body").inner_text()
    if _CONNECTION_ISSUE.search(body):
        raise RuntimeError("claude.ai still shows connection issue after OAuth")
    return "repointed_and_connected"


async def repoint(
    *,
    cdp_url: str,
    from_url: str,
    to_url: str,
    connector_name: str,
    timeout_s: float,
) -> str:
    pw, _browser, context, page = await connect_cdp(cdp_url)
    timeout_ms = int(timeout_s * 1000)
    try:
        page = await _open_connectors_panel(page)
        # Prefer detail for old URL; fall back to new if already repointed.
        try:
            page = await _open_connector_detail(page, connector_name, from_url)
        except RuntimeError:
            page = await _open_connector_detail(page, connector_name, to_url)
            if await page.get_by_text(to_url, exact=False).count():
                return await _reconnect(page, context, to_url, timeout_ms)
            raise

        if await page.get_by_text(to_url, exact=False).count():
            return await _reconnect(page, context, to_url, timeout_ms)

        # Look for Edit before filling.
        edit = page.get_by_role("button", name=re.compile(r"^edit$", re.I))
        if await edit.count() and await edit.first.is_visible():
            await edit.first.click()
            await page.wait_for_timeout(1000)

        await _fill_url(page, to_url)
        await _save_if_present(page)
        return await _reconnect(page, context, to_url, timeout_ms)
    finally:
        await pw.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cdp-url",
        default=os.environ.get("BROWSER_CDP_URL", DEFAULT_CDP_URL),
    )
    parser.add_argument("--from-url", default=DEFAULT_FROM)
    parser.add_argument("--to-url", default=DEFAULT_TO)
    parser.add_argument("--connector-name", default=DEFAULT_CONNECTOR_NAME)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()
    try:
        result = asyncio.run(
            repoint(
                cdp_url=args.cdp_url,
                from_url=args.from_url,
                to_url=args.to_url,
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
