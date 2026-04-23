"""Read the currently-focused http(s) tab from a CDP-attached Chrome."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["ActiveTabResult", "TabInfo", "list_tabs_op", "read_active_tab"]


@dataclass
class ActiveTabResult:
    url: str
    title: str
    content: str
    is_html: bool
    screenshot: bytes | None = None
    screenshot_format: str = "jpeg"
    saved_screenshot_to: str | None = None
    action_failure: dict[str, Any] | None = None


@dataclass
class TabInfo:
    index: int
    title: str
    url: str
    visible: bool
    focused: bool


def _is_http_page(url: str) -> bool:
    """Return True iff *url* is an agent-readable http(s) page."""
    return url.startswith(("http://", "https://"))


async def _classify_page(page: Any) -> tuple[bool, bool]:
    """Return ``(visible, focused)`` for *page* or ``(False, False)`` on failure."""
    try:
        visible = await page.evaluate("document.visibilityState === 'visible'")
        focused = await page.evaluate("document.hasFocus()")
        return bool(visible), bool(focused)
    except Exception as exc:
        logger.warning("Active-tab classify failed for %s: %s", page.url, exc)
        return False, False


async def _select_active_page(ctx: Any) -> Any | None:
    """Pick the active http(s) page from *ctx.pages*; None if none qualify."""
    candidates: list[tuple[Any, bool, bool]] = []
    for page in ctx.pages:
        if not _is_http_page(page.url):
            continue
        visible, focused = await _classify_page(page)
        candidates.append((page, visible, focused))

    if not candidates:
        return None

    for page, visible, focused in candidates:
        if visible and focused:
            return page
    for page, visible, _ in candidates:
        if visible:
            return page
    return candidates[-1][0]


async def read_active_tab(
    *,
    cdp_url: str,
    selector: str | None = None,
    screenshot: bool = False,
    screenshot_format: str = "jpeg",
    screenshot_quality: int = 80,
    save_screenshot_to: str | None = None,
    actions: list[dict[str, Any]] | None = None,
) -> ActiveTabResult:
    """Read the currently-focused tab from the CDP-attached Chrome at *cdp_url*."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright is not installed — run: pip install playwright && playwright install chromium"
        ) from exc

    # Reuse screenshot helpers shared by URL-driven fetch paths.
    from .browser import maybe_screenshot, save_screenshot_file

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp_url)
        ctx = browser.contexts[0] if browser.contexts else None
        if ctx is None:
            raise RuntimeError("No browser context available on attached Chrome")

        page = await _select_active_page(ctx)
        if page is None:
            raise RuntimeError(
                "No http(s) page open in attached Chrome — only chrome:// or "
                "extension targets present"
            )

        action_failure: dict[str, Any] | None = None
        if actions:
            from .actions import ActionError, execute_action

            for idx, act in enumerate(actions):
                try:
                    await execute_action(page, act)
                except ActionError as exc:
                    action_failure = {
                        "error": str(exc),
                        "failed_at": idx,
                        "last_url": page.url,
                    }
                    break
                except Exception as exc:
                    action_failure = {
                        "error": f"Action {idx} failed: {exc}",
                        "failed_at": idx,
                        "last_url": page.url,
                    }
                    break

        url = page.url
        title = await page.title()
        html = await page.content()

        img_bytes, img_format = await maybe_screenshot(
            page, screenshot, screenshot_format, screenshot_quality
        )
        saved_to: str | None = None
        if img_bytes and save_screenshot_to:
            saved_to = save_screenshot_file(img_bytes, save_screenshot_to)

        if selector:
            elements = await page.query_selector_all(selector)
            parts = [t for el in elements if (t := (await el.inner_text()).strip())]
            if parts:
                return ActiveTabResult(
                    url=url,
                    title=title,
                    content="\n\n".join(parts),
                    is_html=False,
                    screenshot=img_bytes,
                    screenshot_format=img_format,
                    saved_screenshot_to=saved_to,
                    action_failure=action_failure,
                )

        return ActiveTabResult(
            url=url,
            title=title,
            content=html,
            is_html=True,
            screenshot=img_bytes,
            screenshot_format=img_format,
            saved_screenshot_to=saved_to,
            action_failure=action_failure,
        )


async def list_tabs_op(*, cdp_url: str) -> list[TabInfo]:
    """Return metadata for all open http(s) tabs in the CDP-attached Chrome."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright is not installed — run: pip install playwright && playwright install chromium"
        ) from exc

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp_url)
        ctx = browser.contexts[0] if browser.contexts else None
        if ctx is None:
            raise RuntimeError("No browser context available on attached Chrome")

        tabs: list[TabInfo] = []
        for idx, page in enumerate(ctx.pages):
            if not _is_http_page(page.url):
                continue
            visible, focused = await _classify_page(page)
            title = await page.title()
            tabs.append(
                TabInfo(
                    index=idx,
                    title=title,
                    url=page.url,
                    visible=visible,
                    focused=focused,
                )
            )
        return tabs
