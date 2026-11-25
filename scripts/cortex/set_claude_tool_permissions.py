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
from playwright.async_api import Page  # noqa: E402

_SCRIPTS_CORTEX = Path(__file__).resolve().parent
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))
from restore_claude_mcp_connector import (  # noqa: E402
    _open_connectors_panel,
    _row_matching,
)

DEFAULT_MCP_URL = "https://mcp.k-1.me/mcp/life"
DEFAULT_CONNECTOR_NAME = "toys"


def _host(mcp_url: str) -> str:
    """Return the hostname used to identify a connector row."""
    return mcp_url.replace("https://", "").replace("http://", "").split("/")[0]




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
