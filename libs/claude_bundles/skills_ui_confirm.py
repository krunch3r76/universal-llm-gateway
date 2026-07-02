"""Replace-confirm dialog and page-wide modal button clicks."""

from __future__ import annotations

import re
import sys

from playwright.async_api import Locator, Page

_REPLACE_CONFIRM_TITLE = re.compile(r'Replace ".+" skill\?', re.I)
_REPLACE_BODY = re.compile(r"can.?t be restored", re.I)
_UPLOAD_REPLACE_BTN = re.compile(r"upload and replace", re.I)


async def replace_confirm_open(page: Page) -> bool:
    """True only when the replace *dialog copy* is visible (not a stale button node)."""
    for loc in (
        page.get_by_text(_REPLACE_CONFIRM_TITLE),
        page.get_by_text(_REPLACE_BODY),
    ):
        if await loc.count() and await loc.first.is_visible():
            return True
    return False


async def replace_confirm_root(page: Page) -> Locator | None:
    for sel in (
        page.locator('[data-state="open"]').filter(has_text=_REPLACE_CONFIRM_TITLE),
        page.locator('[data-state="open"]').filter(has_text=_REPLACE_BODY),
    ):
        if await sel.count():
            ov = sel.last
            if await ov.is_visible():
                return ov
    return None


async def wait_replace_confirm_closed(page: Page, *, timeout_ms: int = 20_000) -> bool:
    for _ in range(timeout_ms // 250):
        if not await replace_confirm_open(page):
            return True
        await page.wait_for_timeout(250)
    return False


async def click_labeled_button(
    page: Page,
    pattern: re.Pattern[str],
    *,
    label: str,
    scope: Locator | None = None,
) -> bool:
    """Click a visible button; optional scope (e.g. one modal overlay)."""
    roots: list[Locator | Page] = [scope] if scope is not None else []
    roots.append(page)

    seen: set[str] = set()
    for root in roots:
        if root is None:
            continue
        locators = [
            root.get_by_role("button", name=pattern),
            root.locator("button").filter(has_text=pattern),
            root.locator('[role="button"]').filter(has_text=pattern),
        ]
        for loc in locators:
            for i in range(await loc.count()):
                btn = loc.nth(i)
                if not await btn.is_visible():
                    continue
                text = " ".join((await btn.inner_text()).split())
                if not text or text in seen:
                    continue
                if not pattern.search(text):
                    continue
                seen.add(text)
                print(f"TRY {label}: {text!r}", file=sys.stderr)
                for force in (False, True):
                    try:
                        await btn.scroll_into_view_if_needed()
                        await btn.click(force=force, timeout=12_000)
                        mode = "force" if force else "ok"
                        print(f"CLICK {label} ({mode}) — {text!r}", file=sys.stderr)
                        await page.wait_for_timeout(800)
                        return True
                    except Exception as exc:
                        tag = "force" if force else "click"
                        print(f"WARN {label} {tag} failed on {text!r}: {exc}", file=sys.stderr)
    return False


async def wait_replace_confirm(page: Page, *, timeout_ms: int = 20_000) -> bool:
    for _ in range(timeout_ms // 250):
        if await replace_confirm_open(page):
            return True
        await page.wait_for_timeout(250)
    return False


async def _click_replace_button(page: Page, modal: Locator | None) -> bool:
    """Click Upload and replace — re-query each attempt (React detaches during animation)."""
    await page.wait_for_timeout(500)
    for attempt in range(4):
        if not await replace_confirm_open(page):
            return True
        if attempt:
            await page.wait_for_timeout(800)
            modal = await replace_confirm_root(page)

        root: Locator | Page = modal if modal is not None else page
        strategies: list[Locator] = [
            root.get_by_role("button", name="Upload and replace"),
            root.locator("button").filter(has_text=_UPLOAD_REPLACE_BTN),
        ]
        if modal is None:
            strategies.insert(0, page.get_by_role("button", name="Upload and replace"))

        clicked = False
        for loc in strategies:
            if not await loc.count():
                continue
            btn = loc.last if await loc.count() > 1 else loc.first
            if not await btn.is_visible():
                continue
            for mode, force in (("ok", False), ("force", True), ("js", None)):
                try:
                    if mode == "js":
                        await btn.evaluate("el => el.click()")
                    else:
                        await btn.click(force=force, timeout=8000)
                    print(f"CLICK Upload and replace ({mode})", file=sys.stderr)
                    clicked = True
                    break
                except Exception as exc:
                    if attempt == 3:
                        print(f"WARN Upload and replace {mode}: {exc}", file=sys.stderr)
            if clicked:
                return True
    return not await replace_confirm_open(page)


async def click_replace_confirm(page: Page) -> bool:
    if not await replace_confirm_open(page):
        return False

    for _ in range(3):
        if not await replace_confirm_open(page):
            return True
        modal = await replace_confirm_root(page)
        if await _click_replace_button(page, modal):
            if await wait_replace_confirm_closed(page, timeout_ms=5000):
                return True
        if not await replace_confirm_open(page):
            return True
        await page.wait_for_timeout(600)

    if await replace_confirm_open(page):
        modal = await replace_confirm_root(page)
        if modal is not None:
            texts = [
                " ".join(t.split())
                for t in await modal.locator("button").all_inner_texts()
                if t.strip()
            ]
            print(f"WARN replace confirm stuck — modal buttons: {texts!r}", file=sys.stderr)
        else:
            print("WARN replace confirm stuck — no modal root located", file=sys.stderr)
        return False
    return True
