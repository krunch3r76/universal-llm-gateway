"""Playwright stealth browser for CF-protected page fetching.

Supports headed mode (real display) for solving Cloudflare Turnstile
challenges that reject software-rendered headless environments.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_CF_INDICATORS = (
    "Just a moment...",
    "Checking your browser",
    "cf-browser-verification",
    "challenge-platform",
)

_CF_TITLE = "Just a moment"


def is_cf_challenge(html: str) -> bool:
    """Detect Cloudflare challenge page from early HTML content."""
    head = html[:3000]
    return any(ind in head for ind in _CF_INDICATORS)


@dataclass
class BrowserResult:
    url: str
    title: str
    content: str
    status: int
    cf_bypassed: bool
    is_html: bool
    screenshot: bytes | None = None
    screenshot_format: str = "jpeg"


def _same_host(left: str, right: str) -> bool:
    """Return True when both URLs point at the same hostname."""
    return urlparse(left).hostname == urlparse(right).hostname


async def _click_turnstile(page: object) -> bool:
    """Find and click the Turnstile checkbox iframe. Returns True if clicked."""
    for frame in page.frames:  # type: ignore[attr-defined]
        if "turnstile" not in frame.url:
            continue
        try:
            el = await frame.frame_element()
            box = await el.bounding_box()
            if box:
                cx = box["x"] + 26
                cy = box["y"] + box["height"] / 2
                await page.mouse.click(cx, cy)  # type: ignore[attr-defined]
                logger.info("Clicked Turnstile checkbox at (%.0f, %.0f)", cx, cy)
                return True
        except Exception as exc:
            logger.warning("Failed to click Turnstile: %s", exc)
    return False


async def _wait_for_cf_resolution(page: object, timeout_ms: int) -> bool:
    """Wait for CF challenge page to resolve. Returns True if resolved."""
    try:
        await page.wait_for_function(  # type: ignore[attr-defined]
            f"() => !document.title.includes('{_CF_TITLE}')",
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


async def download_with_browser(
    url: str,
    *,
    save_to: str,
    cdp_url: str,
    timeout_ms: int = 60_000,
) -> dict[str, object]:
    """Download a file via the authenticated CDP-attached Chrome session.

    Navigates to *url* using the live browser (with its existing cookies/session),
    captures the download event triggered by Scribd-style signed-URL redirects,
    and writes the bytes to *save_to* on the local filesystem.

    Works for any site where the user is already authenticated in the attached
    Chrome — Scribd, court PACER portals, etc.

    Returns ``{"saved_to": path, "size": bytes, "url": final_url}``.
    """
    import pathlib

    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright is not installed — run: pip install playwright && playwright install chromium"
        ) from exc

    save_path = pathlib.Path(save_to)
    save_path.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp_url)
        try:
            ctx = browser.contexts[0] if browser.contexts else await browser.new_context()

            # Allow downloads so Playwright captures them instead of blocking.
            await ctx.grant_permissions([])
            page = await ctx.new_page()

            try:
                async with page.expect_download(timeout=timeout_ms) as dl_info:
                    # navigate — Scribd redirect chain ends in a PDF download trigger
                    await page.goto(url, timeout=timeout_ms, wait_until="commit")

                download = await dl_info.value
                await download.save_as(str(save_path))
                size = save_path.stat().st_size
                logger.info("Downloaded %s → %s (%d bytes)", url, save_path, size)
                return {"saved_to": str(save_path), "size": size, "url": download.url}

            except Exception:
                # Fallback: some sites serve PDFs as inline navigation (no download event).
                # Capture the response bytes directly.
                logger.info("No download event for %s — capturing response bytes", url)
                resp = await page.goto(url, timeout=timeout_ms, wait_until="networkidle")
                if resp is None:
                    raise RuntimeError(f"No response navigating to {url}")
                body = await resp.body()
                save_path.write_bytes(body)
                size = len(body)
                logger.info("Captured response %s → %s (%d bytes)", url, save_path, size)
                return {"saved_to": str(save_path), "size": size, "url": page.url}
        finally:
            if not page.is_closed():
                await page.close()


async def fetch_with_browser(
    url: str,
    *,
    timeout_ms: int = 30_000,
    cf_wait_ms: int = 30_000,
    selector: str | None = None,
    headless: bool | None = None,
    cdp_url: str | None = None,
    screenshot: bool = False,
    screenshot_format: str = "jpeg",
    screenshot_quality: int = 80,
) -> BrowserResult:
    """Fetch *url* with Chromium + stealth patches.

    When *cdp_url* is set, attach to an already-running Chrome/Chromium
    session via the DevTools Protocol. This is the most reliable path for
    CF-protected sites because it reuses a real browser profile that already
    solved the challenge manually.

    When *headless* is None (default), uses headed mode if DISPLAY is set,
    headless otherwise. Headed mode with a real GPU display is required to
    pass Cloudflare Turnstile's verification fingerprinting when launching
    a fresh browser.

    CF bypass flow:
      1. Load page, detect CF challenge
      2. Wait briefly for auto-resolution (non-interactive challenges)
      3. If still blocked, find and click the Turnstile checkbox
      4. Wait for verification to complete
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright is not installed — run: pip install playwright && playwright install chromium"
        ) from exc

    stealth_cfg = None
    try:
        from playwright_stealth import Stealth  # type: ignore[import-untyped]

        stealth_cfg = Stealth()
    except ImportError:
        logger.info("playwright_stealth not available — running without stealth")

    if headless is None:
        headless = not bool(os.environ.get("DISPLAY"))

    async with async_playwright() as p:
        attached_browser = bool(cdp_url)
        if cdp_url:
            logger.info("Attaching to live Chrome via CDP: %s", cdp_url)
            browser = await p.chromium.connect_over_cdp(cdp_url)
        else:
            browser = await p.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
        try:
            if attached_browser:
                ctx = (
                    browser.contexts[0]
                    if browser.contexts
                    else await browser.new_context()
                )
                existing_page = next(
                    (pg for pg in ctx.pages if _same_host(pg.url, url)),
                    None,
                )
                page = (
                    existing_page if existing_page is not None else await ctx.new_page()
                )
            else:
                ctx = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1920, "height": 1080},
                )
                page = await ctx.new_page()
                if stealth_cfg:
                    await stealth_cfg.apply_stealth_async(page)

            need_navigation = page.url != url
            if need_navigation:
                resp = await page.goto(
                    url, timeout=timeout_ms, wait_until="domcontentloaded"
                )
            else:
                resp = None
            status = resp.status if resp else 0
            html = await page.content()

            cf_bypassed = False
            if is_cf_challenge(html):
                logger.info("CF challenge detected for %s", url)
                # Phase 1: wait for auto-resolution (non-interactive challenges)
                if await _wait_for_cf_resolution(page, timeout_ms=5000):
                    cf_bypassed = True
                else:
                    # Phase 2: click Turnstile checkbox and wait
                    await page.wait_for_timeout(2000)
                    if await _click_turnstile(page):
                        if await _wait_for_cf_resolution(page, timeout_ms=cf_wait_ms):
                            cf_bypassed = True
                        else:
                            logger.warning(
                                "CF Turnstile verification failed for %s", url
                            )
                    else:
                        logger.warning("No Turnstile widget found for %s", url)

                html = await page.content()

            title = await page.title()
            final_url = page.url

            img_bytes: bytes | None = None
            img_format: str = screenshot_format if screenshot_format in ("png", "jpeg") else "jpeg"
            if screenshot:
                kwargs: dict[str, object] = {"full_page": True, "type": img_format}
                if img_format == "jpeg":
                    kwargs["quality"] = max(1, min(100, screenshot_quality))
                try:
                    img_bytes = await page.screenshot(**kwargs)  # type: ignore[arg-type]
                except Exception as exc:
                    logger.warning("Screenshot failed for %s: %s", url, exc)

            if selector and cf_bypassed:
                elements = await page.query_selector_all(selector)
                parts = [t for el in elements if (t := (await el.inner_text()).strip())]
                if parts:
                    return BrowserResult(
                        url=final_url,
                        title=title,
                        content="\n\n".join(parts),
                        status=status,
                        cf_bypassed=cf_bypassed,
                        is_html=False,
                        screenshot=img_bytes,
                        screenshot_format=img_format,
                    )

            return BrowserResult(
                url=final_url,
                title=title,
                content=html,
                status=status,
                cf_bypassed=cf_bypassed,
                is_html=True,
                screenshot=img_bytes,
                screenshot_format=img_format,
            )
        finally:
            if not attached_browser:
                await browser.close()
