"""claude.ai Skills settings panel — CDP tab pick, open Customize → Skills."""

from __future__ import annotations

import os
import re
import sys

from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright

SKILLS_URL = "https://claude.ai/new#settings/customize-skills"
DEFAULT_CDP_URL = os.environ.get("BROWSER_CDP_URL", "http://127.0.0.1:9222")

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_BROWSE_LABEL = re.compile(r"browse", re.I)
_ADD_LABEL = re.compile(r"add skill|^add\b", re.I)
_CF_MARKERS = (
    "Performing security verification",
    "Verify you are human",
    "challenge_redirect",
    "cf-browser-verification",
)


def _is_skills_url(url: str) -> bool:
    u = url.lower()
    return "customize-skills" in u or "/customize/skills" in u


def chrome_start_hint(*, port: int = 9222, profile_dir=None) -> str:
    from pathlib import Path

    profile = profile_dir or Path.home() / ".gateway" / "claude-ai-chrome-profile"
    return (
        "# Run ON THE CDP HOST (default: Jupiter DISPLAY=:1), not the Cursor remote shell.\n"
        "# Prefer: scripts/cortex/claude-ai-sync-jupiter ensure-chrome\n"
        f"DISPLAY=:1 google-chrome --remote-debugging-port={port} "
        f"--remote-allow-origins=* "
        f'--user-data-dir="{profile}"'
    )


async def _visible_buttons(page: Page, label: re.Pattern[str]) -> Locator:
    by_role = page.get_by_role("button", name=label)
    by_text = page.locator("button").filter(has_text=label)
    if await by_role.count():
        return by_role
    return by_text


async def _first_visible(locator: Locator) -> Locator | None:
    for i in range(await locator.count()):
        btn = locator.nth(i)
        if await btn.is_visible():
            return btn
    return None


async def _skills_table_rows(page: Page) -> int:
    return await page.locator("table tbody tr").count()


async def _skills_table_has_slugs(page: Page) -> bool:
    cells = page.locator("table tbody tr td:first-child")
    if not await cells.count():
        return False
    for raw in await cells.all_inner_texts():
        slug = raw.strip().split("\n")[0].strip().split()[0] if raw.strip() else ""
        if slug and _SLUG_RE.fullmatch(slug):
            return True
    return False


async def _skills_panel_visible(page: Page) -> bool:
    browse = await _first_visible(await _visible_buttons(page, _BROWSE_LABEL))
    add = await _first_visible(await _visible_buttons(page, _ADD_LABEL))
    if browse and add:
        return True
    return _is_skills_url(page.url) and await _skills_table_has_slugs(page)


async def _find_add_button(page: Page) -> Locator | None:
    for loc in (
        page.get_by_role("button", name=re.compile(r"add skill", re.I)),
        page.locator('button[aria-label="Add skill"]'),
        await _visible_buttons(page, _ADD_LABEL),
    ):
        btn = await _first_visible(loc)
        if btn:
            return btn
    return None


async def _find_browse_button(page: Page) -> Locator | None:
    return await _first_visible(await _visible_buttons(page, _BROWSE_LABEL))


async def _pick_best_page(context: BrowserContext) -> Page:
    for page in context.pages:
        if await _skills_panel_visible(page):
            await page.bring_to_front()
            return page
    for page in context.pages:
        if _is_skills_url(page.url):
            await page.bring_to_front()
            return page
    if context.pages:
        return context.pages[0]
    return await context.new_page()


def _tab_list(context: BrowserContext) -> str:
    lines = []
    for i, tab in enumerate(context.pages):
        flag = " *skills*" if _is_skills_url(tab.url) else ""
        lines.append(f"  [{i}] {tab.url}{flag}")
    return "\n".join(lines) if lines else "  (no tabs)"


async def connect_cdp(cdp_url: str) -> tuple[object, Browser, BrowserContext, Page]:
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(cdp_url)
    except Exception as exc:
        await pw.stop()
        raise RuntimeError(
            f"CDP connect failed ({cdp_url}). Start Chrome on the CDP host first.\n"
            f"  Cursor/remote seat → scripts/cortex/claude-ai-sync-jupiter ensure-chrome\n"
            f"  Manual Jupiter:\n{chrome_start_hint()}"
        ) from exc
    if not browser.contexts:
        await pw.stop()
        raise RuntimeError(f"No browser contexts on {cdp_url}")
    context = browser.contexts[0]
    page = await _pick_best_page(context)
    return pw, browser, context, page


async def _page_blocked(page: Page) -> bool:
    url = page.url.lower()
    if "challenge" in url or url.rstrip("/").endswith("login"):
        return True
    try:
        html = await page.content()
    except Exception:
        return True
    head = html[:4000]
    return any(m in head or m in url for m in _CF_MARKERS)


async def slug_in_skills_table(page: Page, slug: str) -> bool:
    """Whether ``slug`` appears in the Customize → Skills table."""
    if slug.lower() in await listed_skill_names(page):
        return True
    row = page.locator("table tbody tr").filter(
        has=page.locator("td").filter(has_text=re.compile(rf"^{re.escape(slug)}$", re.I))
    )
    return await row.count() > 0


async def listed_skill_names(page: Page) -> set[str]:
    if not await _skills_table_has_slugs(page):
        return set()
    cells = page.locator("table tbody tr td:first-child")
    if not await cells.count():
        return set()
    names: list[str] = []
    for raw in await cells.all_inner_texts():
        name = raw.strip().split("\n")[0].strip().split()[0] if raw.strip() else ""
        if name and _SLUG_RE.fullmatch(name):
            names.append(name)
    return {n.lower() for n in names}


async def _reopen_skills_from_hash(page: Page) -> None:
    customize = page.get_by_role("link", name=re.compile(r"customize", re.I))
    if await customize.count() and await customize.first.is_visible():
        await customize.first.click()
        await page.wait_for_timeout(800)

    for skills in (
        page.get_by_role("link", name=re.compile(r"^skills$", re.I)),
        page.locator("a, button, [role='button'], [role='menuitem']").filter(
            has_text=re.compile(r"^skills$", re.I)
        ),
    ):
        btn = await _first_visible(skills)
        if btn:
            await btn.click()
            await page.wait_for_timeout(1500)
            return


async def open_skills_panel(page: Page, context: BrowserContext) -> Page:
    for tab in context.pages:
        await tab.wait_for_timeout(500)
        if await _skills_panel_visible(tab):
            await tab.bring_to_front()
            return tab

    if await _page_blocked(page):
        raise RuntimeError(
            "Cloudflare or login gate active — solve in Chrome manually, then re-run"
        )

    if _is_skills_url(page.url) and not await _skills_panel_visible(page):
        await _reopen_skills_from_hash(page)
        if await _skills_panel_visible(page):
            return page

    if "claude.ai" not in page.url:
        await page.goto("https://claude.ai/new", wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)

    await page.evaluate(
        "() => { window.location.hash = 'settings/customize-skills'; "
        "window.dispatchEvent(new HashChangeEvent('hashchange')); }"
    )
    await page.wait_for_timeout(2500)
    if not await _skills_panel_visible(page):
        await _reopen_skills_from_hash(page)

    if await _skills_panel_visible(page):
        return page

    for tab in context.pages:
        if await _skills_panel_visible(tab):
            await tab.bring_to_front()
            return tab

    raise RuntimeError(
        "Skills panel not open (need Browse+Add or skills table with slugs).\n"
        "In Chrome: Customize → Skills — keep modal open, then re-run.\n"
        f"Open tabs:\n{_tab_list(context)}"
    )


async def _upload_modal_open(page: Page) -> bool:
    title = page.get_by_text("Upload skill", exact=True)
    if not await title.count():
        return False
    return await title.first.is_visible()


async def _file_input(page: Page) -> Locator | None:
    loc = page.locator('input[type="file"]')
    if await loc.count():
        return loc.first
    return None


async def _dismiss_modals(page: Page) -> None:
    for _ in range(8):
        blocking = page.locator('[data-state="open"].fixed')
        if not await blocking.count() and not await _upload_modal_open(page):
            return
        for close in (
            page.locator('[data-state="open"] button[aria-label="Close"]'),
            page.locator('[data-state="open"] button[aria-label="close"]'),
            page.get_by_role("button", name=re.compile(r"^close$", re.I)),
        ):
            if await close.count() and await close.first.is_visible():
                await close.first.click(force=True)
                await page.wait_for_timeout(500)
                break
        else:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(600)


async def debug_cdp(cdp_url: str) -> None:
    pw, _browser, context, page = await connect_cdp(cdp_url)
    try:
        print(f"CDP: {cdp_url}", file=sys.stderr)
        print(f"Tabs:\n{_tab_list(context)}", file=sys.stderr)
        for i, tab in enumerate(context.pages):
            await tab.wait_for_timeout(300)
            panel = await _skills_panel_visible(tab)
            add = await _find_add_button(tab)
            browse = await _find_browse_button(tab)
            rows = await _skills_table_rows(tab)
            slugs = len(await listed_skill_names(tab))
            print(
                f"  tab[{i}] panel={panel} browse={browse is not None} "
                f"add={add is not None} table_rows={rows} slugs={slugs}",
                file=sys.stderr,
            )
        print(f"Selected tab: {page.url}", file=sys.stderr)
    finally:
        await pw.stop()


async def prepare_session(cdp_url: str) -> None:
    import asyncio

    pw, _browser, context, page = await connect_cdp(cdp_url)
    try:
        print(f"Connected to Chrome via {cdp_url}", file=sys.stderr)
        print(
            "Open Customize → Skills (Browse + Add visible), then press Enter...",
            file=sys.stderr,
        )
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, sys.stdin.readline)
        page = await open_skills_panel(page, context)
        existing = await listed_skill_names(page)
        print(f"OK — tab {page.url} — {len(existing)} skill(s) in table", file=sys.stderr)
    finally:
        await pw.stop()
