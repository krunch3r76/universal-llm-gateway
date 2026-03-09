"""Browser automation tools — Playwright Firefox with host cookie injection.

Exposes browser control tools to Claude via FastMCP. Lazily initializes a
headless Firefox instance and injects cookies from the user's mounted
Firefox profile to enable authenticated browsing (e.g. Upwork).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import shutil
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp.types import ImageContent
from mcp_events import record
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_PROFILE_DIR = Path("/data/firefox_profile")
_MAX_TEXT_LENGTH = 100_000
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
)


class BrowserSession:
    """Persistent lazy-initialized Playwright browser session."""

    def __init__(self) -> None:
        self.pw: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._lock: asyncio.Lock = asyncio.Lock()

    def _get_cookies(self) -> list[dict[str, Any]]:
        """Extract cookies from mounted Firefox profile (read-only)."""
        if not _PROFILE_DIR.exists():
            logger.warning("Firefox profile dir %s not mounted", _PROFILE_DIR)
            return []

        db_orig = _PROFILE_DIR / "cookies.sqlite"
        if not db_orig.exists():
            logger.warning("cookies.sqlite not found in profile")
            return []

        tmp_db_files: list[Path] = []
        try:
            for ext in ("", "-wal", "-shm"):
                src = _PROFILE_DIR / f"cookies.sqlite{ext}"
                if src.exists():
                    dest = Path(f"/tmp/cookies.sqlite{ext}")
                    shutil.copy2(src, dest)
                    tmp_db_files.append(dest)
        except OSError:
            logger.exception("Failed to copy Firefox cookies DB files to /tmp")
            return []

        cookies: list[dict[str, Any]] = []
        try:
            with sqlite3.connect("/tmp/cookies.sqlite") as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(
                    "SELECT name, value, host AS domain, path, "
                    "expiry AS expires, isSecure AS secure, "
                    "isHttpOnly AS httpOnly FROM moz_cookies"
                )
                for row in cur.fetchall():
                    cookie = dict(row)
                    cookie["secure"] = bool(cookie["secure"])
                    cookie["httpOnly"] = bool(cookie["httpOnly"])
                    if not cookie["path"]:
                        cookie["path"] = "/"
                    cookie["sameSite"] = "Lax"
                    cookies.append(cookie)
            logger.info("Loaded %d cookies from Firefox profile", len(cookies))
        except sqlite3.Error:
            logger.exception("Failed to read cookies DB")
        finally:
            for tmp_file in tmp_db_files:
                try:
                    tmp_file.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Failed to remove temporary cookie file %s", tmp_file)

        return cookies

    async def ensure_active(self) -> Page:
        """Return the active page, initializing browser on first call."""
        async with self._lock:
            if self.page and not self.page.is_closed():
                return self.page

            logger.info("Initializing Playwright Firefox session")
            record("mcp.browser.session.started")

            if not self.pw:
                self.pw = await async_playwright().start()

            if not self.browser:
                self.browser = await self.pw.firefox.launch(headless=True)

            if self.context:
                await self.context.close()

            self.context = await self.browser.new_context(
                user_agent=_USER_AGENT,
                viewport={"width": 1280, "height": 800},
            )

            cookies = self._get_cookies()
            if cookies:
                try:
                    await self.context.add_cookies(cookies)
                    record("mcp.browser.cookies.loaded", count=len(cookies))
                except Exception:
                    logger.exception("Failed to inject cookies into Playwright")

            self.page = await self.context.new_page()
            return self.page

    async def refresh_session(self) -> None:
        """Tear down context to force cookie re-read on next call."""
        async with self._lock:
            if self.page and not self.page.is_closed():
                await self.page.close()
            self.page = None
            if self.context:
                await self.context.close()
            self.context = None
            record("mcp.browser.session.refreshed")


_session = BrowserSession()


def register_browser_tools(mcp: FastMCP) -> None:
    """Register Playwright browser tools on *mcp*."""

    def _record_action_result(action: str, status: str, **payload: object) -> None:
        record("mcp.browser.action.result", action=action, status=status, **payload)

    @mcp.tool()
    async def browser_navigate(url: str) -> dict[str, str]:
        """Navigate to a URL. Returns page title and final URL."""
        record("mcp.browser.action", action="navigate", url=url)
        page = await _session.ensure_active()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            _record_action_result("navigate", "success")
            return {
                "status": "success",
                "url": page.url,
                "title": await page.title(),
            }
        except Exception as e:
            logger.exception("browser_navigate failed")
            _record_action_result("navigate", "failed", error=str(e))
            return {"error": str(e), "url": url}

    @mcp.tool()
    async def browser_get_content() -> dict[str, str | bool]:
        """Get visible text content of the current page."""
        record("mcp.browser.action", action="get_content")
        page = await _session.ensure_active()
        try:
            text = await page.evaluate("() => document.body.innerText")
            if not isinstance(text, str):
                text = ""
            _record_action_result("get_content", "success")
            return {
                "text": text[:_MAX_TEXT_LENGTH],
                "truncated": len(text) > _MAX_TEXT_LENGTH,
                "url": page.url,
            }
        except Exception as e:
            logger.exception("browser_get_content failed")
            _record_action_result("get_content", "failed", error=str(e))
            return {"error": str(e)}

    @mcp.tool()
    async def browser_click(selector: str) -> dict[str, str]:
        """Click an element matching a CSS selector."""
        record("mcp.browser.action", action="click", selector=selector)
        page = await _session.ensure_active()
        try:
            await page.click(selector, timeout=5000)
            _record_action_result("click", "success")
            return {"status": "success", "selector": selector}
        except Exception as e:
            logger.exception("browser_click failed")
            _record_action_result("click", "failed", error=str(e))
            return {"error": str(e)}

    @mcp.tool()
    async def browser_fill(selector: str, value: str) -> dict[str, str]:
        """Fill a text input matching a CSS selector."""
        record("mcp.browser.action", action="fill", selector=selector)
        page = await _session.ensure_active()
        try:
            await page.fill(selector, value, timeout=5000)
            _record_action_result("fill", "success")
            return {"status": "success", "selector": selector}
        except Exception as e:
            logger.exception("browser_fill failed")
            _record_action_result("fill", "failed", error=str(e))
            return {"error": str(e)}

    @mcp.tool()
    async def browser_screenshot() -> ImageContent | dict[str, str]:
        """Take a full-page screenshot, returned directly to vision context."""
        record("mcp.browser.action", action="screenshot")
        page = await _session.ensure_active()
        try:
            png_bytes = await page.screenshot(full_page=True)
            _record_action_result("screenshot", "success")
            return ImageContent(type="image", data=base64.b64encode(png_bytes).decode(), mimeType="image/png")
        except Exception as e:
            logger.exception("browser_screenshot failed")
            _record_action_result("screenshot", "failed", error=str(e))
            return {"error": str(e)}

    @mcp.tool()
    async def browser_refresh_session() -> dict[str, str]:
        """Drop browser context and re-read Firefox cookies on next action.

        Call this if you detect a login wall or stale session.
        """
        record("mcp.browser.action", action="refresh_session")
        await _session.refresh_session()
        _record_action_result("refresh_session", "success")
        return {
            "status": "success",
            "message": "Session cleared. Next browser action will reload cookies from host Firefox.",
        }
