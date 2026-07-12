"""Uninstall skills from claude.ai Customize → Skills via CDP."""

from __future__ import annotations

import re
from dataclasses import dataclass

from playwright.async_api import Page

from claude_bundles.skills_ui_panel import (
    _dismiss_modals,
    connect_cdp,
    listed_skill_names,
    open_skills_panel,
    slug_in_skills_table,
)


@dataclass(frozen=True)
class UninstallResult:
    slug: str
    status: str  # uninstalled | absent | failed
    detail: str = ""


async def _open_skill_detail(page: Page, slug: str) -> None:
    rows = page.locator("table tbody tr")
    for i in range(await rows.count()):
        raw = await rows.nth(i).locator("td").first.inner_text()
        name = raw.strip().split("\n")[0].strip().split()[0] if raw.strip() else ""
        if name.lower() == slug.lower():
            await rows.nth(i).click()
            await page.wait_for_timeout(800)
            return
    row = page.locator("table tbody tr", has_text=slug).first
    await row.click()
    await page.wait_for_timeout(800)


async def _click_menu_uninstall(page: Page) -> str | None:
    """Return None on success; error detail string on failure."""
    menu_roots = (
        page.locator('[role="menu"]'),
        page.locator('[data-radix-menu-content]'),
    )
    for root in menu_roots:
        if not await root.count():
            continue
        for i in range(await root.count()):
            menu = root.nth(i)
            if not await menu.is_visible():
                continue
            for pat in (r"^uninstall$", r"uninstall", r"delete", r"remove"):
                item = menu.get_by_role("menuitem", name=re.compile(pat, re.I))
                if not await item.count():
                    item = menu.get_by_text(re.compile(pat, re.I))
                if not await item.count():
                    continue
                for j in range(await item.count()):
                    el = item.nth(j)
                    if await el.is_visible():
                        await el.click()
                        return None
    candidates = (
        page.get_by_role("menuitem", name=re.compile(r"^uninstall$", re.I)),
        page.get_by_role("menuitem", name=re.compile(r"uninstall", re.I)),
        page.get_by_text(re.compile(r"^uninstall$", re.I)),
    )
    for loc in candidates:
        if not await loc.count():
            continue
        for i in range(await loc.count()):
            item = loc.nth(i)
            if not await item.is_visible():
                continue
            await item.click()
            return None
    labels: list[str] = []
    menu_loc = page.locator('[role="menuitem"], [data-radix-collection-item]')
    for i in range(await menu_loc.count()):
        el = menu_loc.nth(i)
        if await el.is_visible():
            labels.append((await el.inner_text()).strip())
    return f"Uninstall menuitem missing; visible={[x for x in labels if x]!r}"


async def uninstall_skill_on_page(page: Page, slug: str) -> UninstallResult:
    """Uninstall one skill already open on the Skills panel.

    UI path (2026-07-11 live verify): row → detail → More options → Uninstall → confirm.
    """
    if not await slug_in_skills_table(page, slug):
        return UninstallResult(slug=slug, status="absent", detail="not listed on UI")

    await _open_skill_detail(page, slug)
    more = page.get_by_role(
        "button", name=re.compile(rf"More options for {re.escape(slug)}", re.I)
    )
    if not await more.count():
        return UninstallResult(slug=slug, status="failed", detail="More options button missing")
    await more.first.click()
    await page.wait_for_timeout(1500)

    menu_err = await _click_menu_uninstall(page)
    if menu_err:
        return UninstallResult(slug=slug, status="failed", detail=menu_err)
    await page.wait_for_timeout(700)

    confirmed = False
    for pat in (r"uninstall", r"delete", r"remove", r"confirm"):
        btns = page.get_by_role("button", name=re.compile(pat, re.I))
        for i in range(await btns.count()):
            btn = btns.nth(i)
            if not await btn.is_visible():
                continue
            label = (await btn.inner_text()).strip().lower()
            if any(tok in label for tok in ("uninstall", "delete", "remove", "confirm")):
                await btn.click()
                confirmed = True
                break
        if confirmed:
            break
    if not confirmed:
        return UninstallResult(slug=slug, status="failed", detail="confirm button not found")

    await page.wait_for_timeout(1500)
    return UninstallResult(slug=slug, status="uninstalled")


async def uninstall_skills(
    *,
    cdp_url: str,
    slugs: list[str],
    continue_on_error: bool = False,
) -> list[UninstallResult]:
    """Uninstall one or more retired skills from the claude.ai Skills table."""
    pw, _browser, context, page = await connect_cdp(cdp_url)
    results: list[UninstallResult] = []
    try:
        page = await open_skills_panel(page, context)
        await _dismiss_modals(page)
        for slug in slugs:
            page = await open_skills_panel(page, context)
            await _dismiss_modals(page)
            try:
                result = await uninstall_skill_on_page(page, slug)
                if result.status == "uninstalled":
                    page = await open_skills_panel(page, context)
                    remaining = {n.lower() for n in await listed_skill_names(page)}
                    if slug.lower() in remaining:
                        result = UninstallResult(
                            slug=slug,
                            status="failed",
                            detail="still listed after uninstall",
                        )
            except Exception as exc:  # noqa: BLE001 — per-slug isolation
                result = UninstallResult(slug=slug, status="failed", detail=str(exc))
            results.append(result)
            if result.status == "failed" and not continue_on_error:
                break
            page = await open_skills_panel(page, context)
            await _dismiss_modals(page)
    finally:
        await pw.stop()
    return results
