"""Cloudflare Turnstile challenge detection and bypass helpers.

The bypass flow runs after an initial ``page.goto()`` — we check whether the
returned HTML matches known CF indicators, and if so either wait out a
non-interactive challenge or click the Turnstile checkbox iframe to complete
an interactive one. Callers pass the page and the initial HTML; we return
whether the page has cleared.
"""

from __future__ import annotations

import logging
from typing import Any

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


async def handle_cf_challenge(page: Any, html: str, url: str, cf_wait_ms: int) -> bool:
    """Attempt to resolve a CF challenge. Returns True if the page cleared.

    Strategy: short auto-resolution wait first (non-interactive challenges),
    then a single Turnstile-checkbox click attempt gated on *cf_wait_ms*.
    """
    if not is_cf_challenge(html):
        return False

    logger.info("CF challenge detected for %s", url)
    if await _wait_for_cf_resolution(page, timeout_ms=5000):
        return True

    await page.wait_for_timeout(2000)
    if not await _click_turnstile(page):
        logger.warning("No Turnstile widget found for %s", url)
        return False

    if await _wait_for_cf_resolution(page, timeout_ms=cf_wait_ms):
        return True

    logger.warning("CF Turnstile verification failed for %s", url)
    return False


async def _click_turnstile(page: Any) -> bool:
    """Find and click the Turnstile checkbox iframe. Returns True if clicked."""
    for frame in page.frames:
        if "turnstile" not in frame.url:
            continue
        try:
            el = await frame.frame_element()
            box = await el.bounding_box()
            if box:
                cx = box["x"] + 26
                cy = box["y"] + box["height"] / 2
                await page.mouse.click(cx, cy)
                logger.info("Clicked Turnstile checkbox at (%.0f, %.0f)", cx, cy)
                return True
        except Exception as exc:
            logger.warning("Failed to click Turnstile: %s", exc)
    return False


async def _wait_for_cf_resolution(page: Any, timeout_ms: int) -> bool:
    """Wait for CF challenge page to resolve. Returns True if resolved."""
    try:
        await page.wait_for_function(
            f"() => !document.title.includes('{_CF_TITLE}')",
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False
