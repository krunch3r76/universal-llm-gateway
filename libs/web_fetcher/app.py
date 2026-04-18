"""FastAPI service for browser-based web fetching with Cloudflare bypass.

Run on a host with a clean (residential) IP to bypass CF managed challenges.
Tries plain HTTP first; falls back to headless Chromium with stealth patches.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import time
from typing import Any

import httpx
import trafilatura
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .browser import (
    BrowserResult,
    download_with_browser,
    fetch_with_browser,
    is_cf_challenge,
)

logger = logging.getLogger(__name__)

_MAX_CONCURRENT_BROWSER = 3
_RATE_LIMIT_PER_MINUTE = 30
_HTTPX_TIMEOUT = 15.0
_DEFAULT_MAX_CHARS = 36_000
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class FetchRequest(BaseModel):
    url: str
    selector: str | None = None
    mode: str = "auto"
    max_chars: int = _DEFAULT_MAX_CHARS
    raw: bool = False
    screenshot: bool = False
    screenshot_format: str = "jpeg"
    screenshot_quality: int = 80
    save_to: str | None = (
        None  # local path on the web-fetcher host; triggers binary download
    )
    # Wait for page readiness before running actions / extraction. Shape:
    #   {"type": "selector", "value": "css", "timeout_ms": 10000}
    #   {"type": "networkidle", "timeout_ms": 15000}
    #   {"type": "timeout_ms", "value": 3000}
    wait_for: dict[str, Any] | None = None
    # Sequential browser actions (click/fill/press/select_option/hover/wait_*).
    # When save_to is also set, actions are wrapped in page.expect_download() to
    # capture click-triggered file downloads. See libs/web_fetcher/actions.py.
    actions: list[dict[str, Any]] | None = None
    # Persist the screenshot bytes to this absolute path on the web-fetcher host
    # (mirrors save_to for binaries). Only honored when screenshot=True.
    save_screenshot_to: str | None = None


def create_app(*, headless: bool | None = None) -> FastAPI:
    """Build and return the web-fetcher FastAPI application."""
    app = FastAPI(
        title="Web Fetcher",
        description="Browser-based web fetching with Cloudflare bypass",
    )
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_BROWSER)
    rate_log: list[float] = []
    cdp_url = os.environ.get("BROWSER_CDP_URL", "").strip() or None

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "concurrent_limit": _MAX_CONCURRENT_BROWSER,
            "headless": headless,
            "cdp_url_configured": bool(cdp_url),
        }

    @app.post("/fetch")
    async def fetch(req: FetchRequest) -> dict[str, Any]:
        now = time.monotonic()
        rate_log[:] = [t for t in rate_log if now - t < 60]
        if len(rate_log) >= _RATE_LIMIT_PER_MINUTE:
            raise HTTPException(429, "Rate limit exceeded (30/min)")
        rate_log.append(now)

        async with semaphore:
            return await _do_fetch(req, headless=headless, cdp_url=cdp_url)

    return app


async def _do_fetch(
    req: FetchRequest,
    *,
    headless: bool | None = None,
    cdp_url: str | None = None,
) -> dict[str, Any]:
    """Orchestrate fetch: httpx first (fast), browser fallback (CF bypass).

    Routing:
      * ``save_to`` without ``actions`` → direct-URL download via
        ``download_with_browser`` (existing fast path for Scribd-style flows).
      * ``save_to`` with ``actions``, or any ``wait_for``/``actions``/``save_screenshot_to``
        → ``fetch_with_browser`` which wraps the action sequence in
        ``page.expect_download()`` when ``save_to`` is set.
      * No save_to / no actions → ``_try_httpx`` fast path, then
        ``fetch_with_browser`` for CF / SPA rendering.
    """
    needs_browser_interaction = bool(
        req.wait_for or req.actions or req.save_screenshot_to
    )

    if req.save_to is not None and not req.actions:
        if not cdp_url:
            return {
                "error": "save_to requires browser mode (BROWSER_CDP_URL not configured)",
                "url": req.url,
            }
        try:
            result = await download_with_browser(
                req.url, save_to=req.save_to, cdp_url=cdp_url
            )
            return result
        except Exception as exc:
            logger.error("Download failed for %s: %s", req.url, exc)
            return {"error": f"Download failed: {exc}", "url": req.url}

    if not needs_browser_interaction and req.mode in ("auto", "http"):
        httpx_result = await _try_httpx(req.url)
        if httpx_result is not None and not is_cf_challenge(httpx_result["html"]):
            return _extract_and_format(httpx_result, req, method="httpx")
        if req.mode == "http":
            if httpx_result:
                return _extract_and_format(httpx_result, req, method="httpx")
            return {"error": "HTTP fetch failed", "url": req.url}

    try:
        result = await fetch_with_browser(
            req.url,
            selector=req.selector,
            headless=headless,
            cdp_url=cdp_url,
            screenshot=req.screenshot,
            screenshot_format=req.screenshot_format,
            screenshot_quality=req.screenshot_quality,
            wait_for=req.wait_for,
            actions=req.actions,
            save_to=req.save_to,
            save_screenshot_to=req.save_screenshot_to,
        )
    except RuntimeError as exc:
        return {"error": str(exc), "url": req.url}
    except Exception as exc:
        logger.error("Browser fetch failed for %s: %s", req.url, exc)
        return {"error": f"Browser fetch failed: {exc}", "url": req.url}

    return _format_browser(result, req)


async def _try_httpx(url: str) -> dict[str, Any] | None:
    """Attempt a plain HTTP fetch — fast path, no JS rendering."""
    try:
        async with httpx.AsyncClient(
            timeout=_HTTPX_TIMEOUT, follow_redirects=True
        ) as client:
            resp = await client.get(url, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            return {"html": resp.text, "url": str(resp.url), "status": resp.status_code}
    except Exception as exc:
        logger.info("httpx failed for %s: %s", url, exc)
        return None


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""


def _extract_text(html: str) -> str:
    extracted = trafilatura.extract(
        html, include_links=True, include_tables=True, output_format="txt"
    )
    return extracted if extracted else html


def _extract_and_format(
    result: dict[str, Any], req: FetchRequest, *, method: str
) -> dict[str, Any]:
    html = result["html"]
    title = _extract_title(html)
    content = html if req.raw else _extract_text(html)
    total = len(content)
    return {
        "url": result["url"],
        "title": title,
        "content": content[: req.max_chars],
        "total_chars": total,
        "truncated": total > req.max_chars,
        "method": method,
        "cf_bypassed": False,
    }


def _format_browser(result: BrowserResult, req: FetchRequest) -> dict[str, Any]:
    # Click-triggered download via actions — mirror the direct-URL save_to shape.
    if result.download is not None:
        return dict(result.download)

    if not result.is_html or req.raw:
        content = result.content
    else:
        content = _extract_text(result.content)
    total = len(content)
    out: dict[str, Any] = {
        "url": result.url,
        "title": result.title,
        "content": content[: req.max_chars],
        "total_chars": total,
        "truncated": total > req.max_chars,
        "method": "browser",
        "cf_bypassed": result.cf_bypassed,
    }
    if result.screenshot:
        out["screenshot"] = base64.b64encode(result.screenshot).decode()
        out["screenshot_format"] = result.screenshot_format
    if result.saved_screenshot_to:
        out["saved_screenshot_to"] = result.saved_screenshot_to
    if result.action_failure is not None:
        out["action_failure"] = result.action_failure
    return out
