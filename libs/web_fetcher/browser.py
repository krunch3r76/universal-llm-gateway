"""Playwright stealth browser for CF-protected page fetching.

Supports headed mode (real display) for solving Cloudflare Turnstile
challenges that reject software-rendered headless environments. Also supports
interactive workflows via the ``actions`` parameter — click / fill / etc.
sequences that execute server-side in one atomic call, optionally wrapped in
``page.expect_download()`` when combined with ``save_to`` for click-triggered
PDF or binary captures.
"""

from __future__ import annotations

import logging
import os
import pathlib
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from .actions import apply_wait_for, execute_action
from .cloudflare import handle_cf_challenge, is_cf_challenge

logger = logging.getLogger(__name__)

# Re-exported for callers that only want the detector (e.g. app.py httpx fast path).
__all__ = [
    "BrowserResult",
    "download_with_browser",
    "fetch_with_browser",
    "is_cf_challenge",
]


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
    saved_screenshot_to: str | None = None
    # Populated only when save_to + actions captured a click-triggered download.
    download: dict[str, Any] | None = None
    # Populated on action failure: {error, failed_at, last_url}; also raises upstream
    # when the executor can't cleanly surface the error without aborting the fetch.
    action_failure: dict[str, Any] | None = field(default=None)


def _same_host(left: str, right: str) -> bool:
    """Return True when both URLs point at the same hostname."""
    return urlparse(left).hostname == urlparse(right).hostname


async def download_with_browser(
    url: str,
    *,
    save_to: str,
    cdp_url: str,
    timeout_ms: int = 60_000,
) -> dict[str, object]:
    """Download a file via the authenticated CDP-attached Chrome session.

    Direct-URL path: navigates to *url* and captures a download event triggered
    by the target (Scribd-style signed-URL redirects, etc.). Used when the
    download URL is known in advance and no page interaction is needed.

    For click-triggered downloads (agent clicks a button and a PDF downloads),
    use ``fetch_with_browser(..., save_to=..., actions=[...])`` instead.

    Returns ``{"saved_to": path, "size": bytes, "url": final_url}``.
    """
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
            ctx = (
                browser.contexts[0] if browser.contexts else await browser.new_context()
            )
            await ctx.grant_permissions([])
            page = await ctx.new_page()

            try:
                async with page.expect_download(timeout=timeout_ms) as dl_info:
                    await page.goto(url, timeout=timeout_ms, wait_until="commit")

                download = await dl_info.value
                await download.save_as(str(save_path))
                size = save_path.stat().st_size
                logger.info("Downloaded %s → %s (%d bytes)", url, save_path, size)
                return {"saved_to": str(save_path), "size": size, "url": download.url}

            except Exception:
                # Fallback: some sites serve PDFs as inline navigation (no download event).
                logger.info("No download event for %s — capturing response bytes", url)
                resp = await page.goto(
                    url, timeout=timeout_ms, wait_until="networkidle"
                )
                if resp is None:
                    raise RuntimeError(f"No response navigating to {url}")
                body = await resp.body()
                save_path.write_bytes(body)
                size = len(body)
                logger.info(
                    "Captured response %s → %s (%d bytes)", url, save_path, size
                )
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
    wait_for: dict[str, Any] | None = None,
    actions: list[dict[str, Any]] | None = None,
    save_to: str | None = None,
    save_screenshot_to: str | None = None,
) -> BrowserResult:
    """Fetch *url* with Chromium + stealth patches, optionally driving an action sequence.

    When *cdp_url* is set, attach to an already-running Chrome/Chromium session
    via the DevTools Protocol. This is the most reliable path for CF-protected
    sites and the required path for authenticated workflows (user's live
    profile on the CDP host).

    After navigating and optional CF bypass, the flow is:
      1. Apply *wait_for* spec (selector/networkidle/timeout_ms), if any
      2. Execute *actions* sequentially (click/fill/press/select/hover/wait_*)
      3. If *save_to* is set together with actions, wrap the action sequence in
         ``page.expect_download()`` — whichever action triggers a browser
         download event, capture it and save to *save_to*
      4. Extract content (selector or full HTML) and optional screenshot
      5. If *save_screenshot_to* is set, persist the screenshot bytes to that
         path on the local (CDP host) filesystem

    Returns a :class:`BrowserResult` with ``download`` populated when save_to
    captured a file, and ``action_failure`` populated (plus raised exception
    chain in logs) when an action could not execute.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright is not installed — run: pip install playwright && playwright install chromium"
        ) from exc

    stealth_cfg = None
    try:
        from playwright_stealth import Stealth

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

            cf_bypassed = await handle_cf_challenge(page, html, url, cf_wait_ms)
            if cf_bypassed:
                html = await page.content()

            download_meta: dict[str, Any] | None = None
            action_failure: dict[str, Any] | None = None

            if wait_for:
                try:
                    await apply_wait_for(page, wait_for)
                except Exception as exc:
                    logger.warning("wait_for failed for %s: %s", url, exc)
                    action_failure = {
                        "error": f"wait_for failed: {exc}",
                        "failed_at": -1,
                        "last_url": page.url,
                    }

            if actions and not action_failure:
                download_meta, action_failure = await _run_actions(
                    page, actions, save_to=save_to, timeout_ms=timeout_ms
                )
                html = await page.content()

            title = await page.title()
            final_url = page.url

            img_bytes, img_format = await _maybe_screenshot(
                page, screenshot, screenshot_format, screenshot_quality
            )

            saved_screenshot_to: str | None = None
            if img_bytes and save_screenshot_to:
                saved_screenshot_to = _save_screenshot_file(
                    img_bytes, save_screenshot_to
                )

            if selector and cf_bypassed and not action_failure:
                extracted = await _extract_by_selector(page, selector)
                if extracted:
                    return BrowserResult(
                        url=final_url,
                        title=title,
                        content=extracted,
                        status=status,
                        cf_bypassed=cf_bypassed,
                        is_html=False,
                        screenshot=img_bytes,
                        screenshot_format=img_format,
                        saved_screenshot_to=saved_screenshot_to,
                        download=download_meta,
                        action_failure=action_failure,
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
                saved_screenshot_to=saved_screenshot_to,
                download=download_meta,
                action_failure=action_failure,
            )
        finally:
            if not attached_browser:
                await browser.close()


async def _run_actions(
    page: Any,
    actions: list[dict[str, Any]],
    *,
    save_to: str | None,
    timeout_ms: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Execute *actions* sequentially; optionally capture a click-triggered download.

    Returns ``(download_meta, action_failure)``. At most one is non-None when the
    sequence completes normally; if a download is expected but the sequence
    fails before the download event, ``action_failure`` is populated and
    ``download_meta`` is None.
    """
    if save_to:
        save_path = pathlib.Path(save_to)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            async with page.expect_download(timeout=timeout_ms) as dl_info:
                await _execute_action_sequence(page, actions)
            download = await dl_info.value
            await download.save_as(str(save_path))
            size = save_path.stat().st_size
            logger.info("Click-triggered download → %s (%d bytes)", save_path, size)
            return (
                {"saved_to": str(save_path), "size": size, "url": download.url},
                None,
            )
        except Exception as exc:
            logger.warning("Download capture failed during actions: %s", exc)
            return None, _action_failure_payload(exc, page)

    try:
        await _execute_action_sequence(page, actions)
        return None, None
    except Exception as exc:
        logger.warning("Action sequence failed: %s", exc)
        return None, _action_failure_payload(exc, page)


async def _execute_action_sequence(page: Any, actions: list[dict[str, Any]]) -> None:
    """Run actions in order. Tags the raised exception with ``_failed_at`` index."""
    for idx, action in enumerate(actions):
        try:
            await execute_action(page, action)
        except Exception as exc:
            exc._failed_at = idx
            raise


def _action_failure_payload(exc: Exception, page: Any) -> dict[str, Any]:
    """Build the structured action failure dict returned alongside BrowserResult."""
    failed_at = getattr(exc, "_failed_at", -1)
    last_url = ""
    try:
        last_url = page.url
    except Exception:
        pass
    return {
        "error": str(exc),
        "failed_at": failed_at,
        "last_url": last_url,
    }


async def _capture_root_scroll_state(page: Any) -> dict[str, Any] | None:
    """Snapshot root scroll position and inline overflow styles before screenshot."""
    try:
        return await page.evaluate("""() => {
            const html = document.documentElement;
            const body = document.body;
            const styleState = (el) => ({
                overflow: el?.style?.overflow ?? "",
                overflowX: el?.style?.overflowX ?? "",
                overflowY: el?.style?.overflowY ?? "",
            });
            return {
                scrollX: window.scrollX,
                scrollY: window.scrollY,
                html: styleState(html),
                body: styleState(body),
            };
        }""")
    except Exception as exc:
        logger.debug("Unable to snapshot scroll state before screenshot: %s", exc)
        return None


async def _restore_root_scroll_state(page: Any, state: dict[str, Any] | None) -> None:
    """Restore root scrolling after Playwright full-page screenshot side effects."""
    if state is None:
        return
    try:
        await page.evaluate(
            """(state) => {
                const html = document.documentElement;
                const body = document.body;
                const restore = (el, styles) => {
                    if (!el || !styles) return;
                    el.style.overflow = styles.overflow ?? "";
                    el.style.overflowX = styles.overflowX ?? "";
                    el.style.overflowY = styles.overflowY ?? "";
                };
                restore(html, state.html);
                restore(body, state.body);
                window.scrollTo(state.scrollX ?? 0, state.scrollY ?? 0);
            }""",
            state,
        )
    except Exception as exc:
        logger.warning("Failed to restore scroll state after screenshot: %s", exc)


async def _maybe_screenshot(
    page: Any, screenshot: bool, screenshot_format: str, screenshot_quality: int
) -> tuple[bytes | None, str]:
    """Capture a full-page screenshot when requested, returning (bytes, format)."""
    img_format = screenshot_format if screenshot_format in ("png", "jpeg") else "jpeg"
    if not screenshot:
        return None, img_format
    kwargs: dict[str, object] = {"full_page": True, "type": img_format}
    if img_format == "jpeg":
        kwargs["quality"] = max(1, min(100, screenshot_quality))
    scroll_state = await _capture_root_scroll_state(page)
    try:
        return await page.screenshot(**kwargs), img_format
    except Exception as exc:
        logger.warning("Screenshot failed: %s", exc)
        return None, img_format
    finally:
        await _restore_root_scroll_state(page, scroll_state)


def _save_screenshot_file(img_bytes: bytes, save_screenshot_to: str) -> str:
    """Persist screenshot bytes to the given host path; return the absolute path."""
    path = pathlib.Path(save_screenshot_to)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(img_bytes)
    logger.info("Saved screenshot → %s (%d bytes)", path, len(img_bytes))
    return str(path)


async def _extract_by_selector(page: Any, selector: str) -> str:
    """Concatenate inner_text of all matching elements; empty string if none."""
    elements = await page.query_selector_all(selector)
    parts = [t for el in elements if (t := (await el.inner_text()).strip())]
    return "\n\n".join(parts) if parts else ""
