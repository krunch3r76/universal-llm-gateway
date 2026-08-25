"""claude.ai Skills settings panel — CDP tab pick, open Customize → Skills."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    async_playwright,
)

from claude_bundles.skills_ui_menu import (
    PreflightMenuError,
    assert_add_menu_upload_ready,
    stability_guarded_add_click,
)

SKILLS_URL = "https://claude.ai/new#settings/customize-skills"
DEFAULT_CDP_URL = os.environ.get("BROWSER_CDP_URL", "http://127.0.0.1:9222")

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_BROWSE_LABEL = re.compile(r"browse", re.I)
# Customize → Skills "Add" only — ¬ Cowork composer "Add files, connectors, and more"
# (friction a:27143 upload residue 2026-07-30: ^add\b matched composer Add).
_ADD_LABEL = re.compile(r"add skill|^add$", re.I)
_COMPOSER_ADD_NOISE = re.compile(
    r"files|connectors|photos|screenshot|plugins|more$", re.I
)
_SKILLS_NAV = re.compile(r"skills", re.I)
_UPLOAD_TITLE = re.compile(r"upload\s+skill", re.I)
_CF_MARKERS = (
    "Performing security verification",
    "Verify you are human",
    "challenge_redirect",
    "cf-browser-verification",
)


@dataclass
class NavigationGate:
    """Gate real navigation remount while upload is in-flight or unverified."""

    upload_in_flight: bool = False
    network_verified: bool = True

    def remount_permitted(self) -> bool:
        return not self.upload_in_flight and self.network_verified


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


async def _is_composer_add(btn: Locator) -> bool:
    """True when the button is the chat/Cowork composer + menu, not Skills Add."""
    try:
        aria = (await btn.get_attribute("aria-label")) or ""
        text = (await btn.inner_text()) or ""
    except Exception:
        return False
    blob = f"{aria} {text}".strip()
    return bool(_COMPOSER_ADD_NOISE.search(blob))


async def _skills_panel_visible(page: Page) -> bool:
    browse = await _first_visible(await _visible_buttons(page, _BROWSE_LABEL))
    add = await _first_visible(await _visible_buttons(page, _ADD_LABEL))
    if add and await _is_composer_add(add):
        add = None
    if browse and add:
        return True
    # Cowork CSE tabs often carry #settings/customize-skills + unrelated tables /
    # session-skill chips — do not treat that as the Customize Skills library.
    if "/cowork/" in page.url.lower() and not browse:
        return False
    return _is_skills_url(page.url) and await _skills_table_has_slugs(page)


async def _find_add_button(page: Page) -> Locator | None:
    if not await _skills_panel_visible(page):
        return None
    for loc in (
        page.get_by_role("button", name=re.compile(r"add skill", re.I)),
        page.locator('button[aria-label="Add skill"]'),
        await _visible_buttons(page, _ADD_LABEL),
    ):
        for i in range(await loc.count()):
            btn = loc.nth(i)
            if not await btn.is_visible():
                continue
            if await _is_composer_add(btn):
                continue
            return btn
    return None


async def _find_browse_button(page: Page) -> Locator | None:
    return await _first_visible(await _visible_buttons(page, _BROWSE_LABEL))


async def _pick_best_page(context: BrowserContext) -> Page:
    for page in context.pages:
        if await _skills_panel_visible(page):
            await page.bring_to_front()
            return page
    # Prefer /new#settings over /cowork/…#settings (hash alone is not the panel).
    for page in context.pages:
        u = page.url.lower()
        if _is_skills_url(u) and "/cowork/" not in u:
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


async def panel_state_summary(page: Page, context: BrowserContext) -> str:
    add = await _find_add_button(page)
    browse = await _find_browse_button(page)
    rows = await _skills_table_rows(page)
    return (
        f"url={page.url} panel={await _skills_panel_visible(page)} "
        f"browse={browse is not None} add={add is not None} rows={rows}\n"
        f"tabs:\n{_tab_list(context)}"
    )


async def slug_in_skills_table(page: Page, slug: str) -> bool:
    """Whether ``slug`` appears in the Customize → Skills table."""
    if slug.lower() in await listed_skill_names(page):
        return True
    pattern = re.compile(rf"\b{re.escape(slug)}\b", re.I)
    rows = page.locator("table tbody tr")
    for i in range(await rows.count()):
        if pattern.search(await rows.nth(i).inner_text()):
            return True
    return False


async def snapshot_slug_row(page: Page, slug: str) -> str | None:
    pattern = re.compile(rf"\b{re.escape(slug)}\b", re.I)
    rows = page.locator("table tbody tr")
    for i in range(await rows.count()):
        text = await rows.nth(i).inner_text()
        if pattern.search(text):
            return text.strip()
    return None


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


async def _dismiss_bottom_tray(page: Page) -> None:
    """Clear composer/bottom-tray overlays that intercept Customize clicks.

    Live failure (2026-07-09 thread 4736): ``df-bottom-tray`` sits above the
    sidebar Customize button and Playwright's actionability check times out
    even though the button is visible. Escape + hide is enough; force-click
    remains a backstop in ``_reopen_skills_from_hash``.
    """
    for _ in range(3):
        tray = page.locator("div.df-bottom-tray")
        if not await tray.count():
            return
        visible = False
        try:
            visible = await tray.first.is_visible()
        except Exception:
            visible = False
        if not visible:
            return
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
        await page.evaluate(
            """() => {
              document.querySelectorAll('div.df-bottom-tray').forEach((el) => {
                el.style.pointerEvents = 'none';
                el.style.visibility = 'hidden';
              });
            }"""
        )
        await page.wait_for_timeout(200)


async def _reopen_skills_from_hash(page: Page) -> None:
    # Hash alone no longer mounts Settings (2026-07 live): Customize is a
    # sidebar *button*, not a link. Click it first so Skills/Connectors nav appears.
    await _dismiss_bottom_tray(page)
    for customize in (
        page.get_by_role("button", name=re.compile(r"customize", re.I)),
        page.get_by_role("link", name=re.compile(r"customize", re.I)),
        page.locator("a, button, [role='button'], [role='menuitem']").filter(
            has_text=re.compile(r"^customize$", re.I)
        ),
    ):
        btn = await _first_visible(customize)
        if btn:
            await btn.click(force=True)
            await page.wait_for_timeout(1200)
            break

    for skills in (
        page.get_by_role("button", name=_SKILLS_NAV),
        page.get_by_role("tab", name=_SKILLS_NAV),
        page.get_by_role("link", name=_SKILLS_NAV),
        page.locator("a, button, [role='button'], [role='menuitem'], [role='tab']").filter(
            has_text=_SKILLS_NAV
        ),
    ):
        btn = await _first_visible(skills)
        if btn:
            await btn.click(force=True)
            await page.wait_for_timeout(1500)
            return


async def _hash_cycle(page: Page) -> None:
    await page.evaluate("() => { window.location.hash = ''; }")
    await page.wait_for_timeout(800)
    await page.evaluate("() => { window.location.hash = 'settings/customize-skills'; }")
    await page.wait_for_timeout(2000)


async def _remount_skills(page: Page) -> None:
    await page.goto("https://claude.ai/new", wait_until="domcontentloaded")
    await page.wait_for_timeout(1500)
    await page.goto(SKILLS_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2500)
    # Hash URL alone no longer mounts the Settings dialog — click Customize→Skills.
    await _reopen_skills_from_hash(page)


async def open_skills_panel(
    page: Page,
    context: BrowserContext,
    *,
    nav_gate: NavigationGate | None = None,
) -> Page:
    for tab in context.pages:
        await tab.wait_for_timeout(300)
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

    await _hash_cycle(page)
    if not await _skills_panel_visible(page):
        await _reopen_skills_from_hash(page)

    if await _skills_panel_visible(page):
        return page

    if nav_gate is None or nav_gate.remount_permitted():
        await _remount_skills(page)
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


async def run_preflight(page: Page, context: BrowserContext) -> None:
    """CDP + panel + Add + selectable Upload menuitem (fail-closed)."""
    if await _page_blocked(page):
        raise RuntimeError(
            "Preflight failed: Cloudflare or login gate — solve in Chrome, then re-run"
        )
    page = await open_skills_panel(page, context)
    add = await _find_add_button(page)
    if add is None:
        raise RuntimeError(
            "Preflight failed: Add button not visible — open Customize → Skills\n"
            + await panel_state_summary(page, context)
        )
    await stability_guarded_add_click(add)
    try:
        await assert_add_menu_upload_ready(page, add)
    except PreflightMenuError as exc:
        raise PreflightMenuError(
            f"{exc}\n{await panel_state_summary(page, context)}",
            exc.inventory,
        ) from exc
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(400)
    if not await _find_add_button(page):
        raise RuntimeError(
            "Preflight failed: Add button lost after dry menu — panel unstable\n"
            + await panel_state_summary(page, context)
        )


async def _upload_modal_root(page: Page) -> Locator | None:
    # Base UI uses data-popup-open; Radix dialogs still use data-state=open.
    overlays = page.locator(
        '[data-popup-open], [role="dialog"], [data-state="open"].fixed'
    )
    for i in range(await overlays.count()):
        ov = overlays.nth(i)
        if not await ov.is_visible():
            continue
        if _UPLOAD_TITLE.search(await ov.inner_text()):
            return ov
    title = page.get_by_text(_UPLOAD_TITLE)
    if await title.count() and await title.first.is_visible():
        parent = title.first.locator(
            "xpath=ancestor::*[@data-popup-open or @data-state='open' or @role='dialog'][1]"
        )
        if await parent.count():
            return parent.first
    return None


async def _recover_panel_spa(
    page: Page,
    context: BrowserContext,
    *,
    nav_gate: NavigationGate | None,
) -> Page:
    await _reopen_skills_from_hash(page)
    await page.wait_for_timeout(1500)
    if not await _skills_panel_visible(page):
        await _hash_cycle(page)
        await page.wait_for_timeout(1500)
    if not await _skills_panel_visible(page):
        page = await open_skills_panel(page, context, nav_gate=nav_gate)
    return page


async def _panel_lost_mid_attempt(page: Page) -> bool:
    if page.url.rstrip("/").endswith("/new") and not _is_skills_url(page.url):
        return True
    return not await _skills_panel_visible(page)


async def _upload_modal_open(page: Page) -> bool:
    return await _upload_modal_root(page) is not None


async def _dismiss_modals(page: Page) -> None:
    for _ in range(8):
        blocking = page.locator('[data-popup-open], [data-state="open"].fixed')
        if not await blocking.count() and not await _upload_modal_open(page):
            return
        for close in (
            page.locator(
                '[data-popup-open] button[aria-label="Close"], '
                '[data-state="open"] button[aria-label="Close"]'
            ),
            page.locator(
                '[data-popup-open] button[aria-label="close"], '
                '[data-state="open"] button[aria-label="close"]'
            ),
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
