"""claude.ai Skills panel — upload dialog and completion wait."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from playwright.async_api import Locator, Page

from claude_bundles.bundle_description import MAX_CLAUDE_AI_DESCRIPTION_LEN
from claude_bundles.skills_ui_confirm import (
    click_labeled_button,
    click_replace_confirm,
    replace_confirm_open,
    replace_confirm_root,
    wait_replace_confirm,
)
from claude_bundles.skills_ui_panel import (
    _dismiss_modals,
    _file_input,
    _find_add_button,
    slug_in_skills_table,
)

_UPLOAD_MENU = re.compile(r"upload a skill", re.I)
_DROP_ZONE = re.compile(r"click to upload|drag and drop", re.I)
_UPLOAD_BTN = re.compile(r"^upload$", re.I)
_REJECT_RE = re.compile(
    r"\b(error|failed|too long|exceeds|maximum|already exists|duplicate|invalid)\b",
    re.I,
)


class ReplaceBlockedError(Exception):
    """Legacy — replace flow uses confirm dialog instead of blocking."""


async def _upload_modal_open(page: Page) -> bool:
    title = page.get_by_text("Upload skill", exact=True)
    if not await title.count():
        return False
    return await title.first.is_visible()


async def _modals_blocking(page: Page) -> bool:
    return await _upload_modal_open(page) or await replace_confirm_open(page)


async def _upload_verified(
    page: Page,
    slug: str,
    rows_before: int,
    *,
    replacing: bool,
    replace_confirmed: bool,
) -> bool:
    if replace_confirmed:
        return True
    if await slug_in_skills_table(page, slug):
        return True
    if replacing:
        return False
    return await page.locator("table tbody tr").count() > rows_before


async def _open_upload_dialog(page: Page) -> Locator:
    """Add → Upload a skill → return file input."""
    if await _upload_modal_open(page):
        ready = await _file_input(page)
        if ready:
            return ready

    await _dismiss_modals(page)

    add_btn = await _find_add_button(page)
    if not add_btn:
        raise RuntimeError(
            "Add button not found — open Customize → Skills (Browse + Add visible), then re-run"
        )
    await add_btn.click()
    await page.wait_for_timeout(600)

    upload_item = page.get_by_role("menuitem", name=_UPLOAD_MENU)
    if not await upload_item.count():
        upload_item = page.locator("[role='menuitem']").filter(has_text=_UPLOAD_MENU)
    if not await upload_item.count():
        raise RuntimeError("Add → Upload a skill menu item not found")
    await upload_item.first.click()

    inp = page.locator('input[type="file"]')
    await inp.first.wait_for(state="attached", timeout=15_000)
    return inp.first


async def _modal_error_text(page: Page) -> str | None:
    if not await _upload_modal_open(page):
        return None
    overlay = page.locator('[data-state="open"].fixed')
    if not await overlay.count():
        return None
    text = await overlay.first.inner_text()
    if "Drag and drop or click to upload" in text and len(text) < 600:
        if not _REJECT_RE.search(text):
            return None
    if _REJECT_RE.search(text):
        return text[:400]
    return None


async def _select_file_in_modal(page: Page, skill_path: Path) -> None:
    """Pick a file via the visible drop zone (hidden input alone often does not bind)."""
    resolved = str(skill_path.resolve())
    drop = page.locator("[data-state='open'].fixed").filter(has_text=_DROP_ZONE)
    if await drop.count():
        trigger = drop.first.get_by_text(_DROP_ZONE)
        if await trigger.count():
            async with page.expect_file_chooser(timeout=15_000) as fc_info:
                await trigger.first.click()
            chooser = await fc_info.value
            await chooser.set_files(resolved)
            return

    inp = page.locator('input[type="file"]')
    if not await inp.count():
        raise RuntimeError("Upload modal has no file input or drop zone")
    await inp.first.set_input_files(resolved)
    await inp.first.evaluate(
        """el => {
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }"""
    )


async def _wait_file_selected(page: Page, skill_path: Path) -> None:
    """Wait until the modal reflects a chosen file (not still on empty drop zone)."""
    basename = skill_path.name
    stem = skill_path.stem
    for _ in range(40):
        if await replace_confirm_open(page):
            return
        if not await _upload_modal_open(page):
            return
        overlay = page.locator('[data-state="open"].fixed')
        if not await overlay.count():
            await page.wait_for_timeout(300)
            continue
        text = await overlay.first.inner_text()
        if basename in text or stem in text:
            return
        if "Drag and drop or click to upload" not in text:
            return
        await page.wait_for_timeout(300)
    raise RuntimeError(
        f"File not accepted in upload modal ({basename}) — drop zone still empty"
    )


async def _upload_modal_root(page: Page) -> Locator | None:
    by_title = page.locator('[data-state="open"].fixed').filter(
        has=page.get_by_text("Upload skill", exact=True)
    )
    if await by_title.count():
        return by_title.first
    if await _upload_modal_open(page):
        ov = page.locator('[data-state="open"].fixed')
        if await ov.count():
            return ov.first
    return None


async def _click_upload_submit(page: Page) -> None:
    """First modal footer: Upload (scoped to upload dialog only)."""
    if await replace_confirm_open(page):
        return
    modal = await _upload_modal_root(page)
    if modal is None:
        return
    if await click_labeled_button(page, _UPLOAD_BTN, label="Upload", scope=modal):
        return
    await page.wait_for_timeout(1200)
    if await replace_confirm_open(page):
        return
    if await _upload_modal_open(page):
        print("WARN Upload button not clicked — upload modal still open", file=sys.stderr)


async def _confirm_replace_flow(page: Page) -> bool:
    """Wait for replace confirm, click through. Returns True if replace confirm closed."""
    if not await wait_replace_confirm(page, timeout_ms=12_000):
        return False
    for _ in range(3):
        if not await replace_confirm_open(page):
            return True
        if await click_replace_confirm(page):
            return True
        await page.wait_for_timeout(1000)
    return not await replace_confirm_open(page)


async def _click_submit_if_present(page: Page, *, replacing: bool = False) -> bool:
    del replacing
    await _click_upload_submit(page)
    return await _confirm_replace_flow(page)


async def _wait_upload_complete(
    page: Page,
    slug: str,
    *,
    replacing: bool = False,
    desc_len: int | None = None,
    rows_before: int = 0,
    retry_submit: object | None = None,
    replace_confirmed: bool = False,
) -> None:
    """Wait until modals clear and slug is verified in the skills table."""
    await page.wait_for_timeout(1500)
    retried = False
    clear_ticks = 0
    for tick in range(120):
        await page.wait_for_timeout(500)
        if await replace_confirm_open(page):
            clear_ticks = 0
            if await click_replace_confirm(page):
                replace_confirmed = True
            continue
        err = await _modal_error_text(page)
        if err:
            raise RuntimeError(f"Upload rejected for {slug}: {err}")

        blocking = await _modals_blocking(page)
        verified = await _upload_verified(
            page, slug, rows_before, replacing=replacing, replace_confirmed=replace_confirmed
        )

        if not blocking:
            clear_ticks += 1
            if verified and clear_ticks >= 2:
                await _dismiss_modals(page)
                return
            if clear_ticks >= 6 and not verified:
                raise RuntimeError(
                    f"Upload did not verify for {slug} — modals closed but slug not in table"
                )
        else:
            clear_ticks = 0

        if (
            not retried
            and tick >= 8
            and await _upload_modal_open(page)
            and retry_submit is not None
            and callable(retry_submit)
        ):
            retried = True
            print(f"RETRY submit for {slug} (upload modal still open)", file=sys.stderr)
            if await retry_submit():
                replace_confirmed = True

    hint = ""
    if await replace_confirm_open(page):
        modal = await replace_confirm_root(page)
        if modal is not None:
            hint = (await modal.inner_text())[:300]
    elif await _upload_modal_open(page):
        overlay = page.locator('[data-state="open"].fixed')
        if await overlay.count():
            hint = (await overlay.first.inner_text())[:300]
    replace_hint = " — re-upload of table skill" if replacing else ""
    over = (
        f" (description {desc_len} chars > {MAX_CLAUDE_AI_DESCRIPTION_LEN})"
        if desc_len and desc_len > MAX_CLAUDE_AI_DESCRIPTION_LEN
        else ""
    )
    stage = "replace confirm" if await replace_confirm_open(page) else "upload modal"
    raise RuntimeError(
        f"Upload timed out for {slug}{over}{replace_hint} ({stage} still open): {hint}"
    )


async def upload_one_skill(
    page: Page,
    skill_path: Path,
    slug: str,
    *,
    replacing: bool = False,
    screenshot_dir: Path | None = None,
    desc_len: int | None = None,
) -> Page:
    rows_before = await page.locator("table tbody tr").count()
    await _open_upload_dialog(page)
    await _select_file_in_modal(page, skill_path)
    await _wait_file_selected(page, skill_path)
    replace_confirmed = await _click_submit_if_present(page, replacing=replacing)
    await _wait_upload_complete(
        page,
        slug,
        replacing=replacing,
        desc_len=desc_len,
        rows_before=rows_before,
        replace_confirmed=replace_confirmed,
        retry_submit=lambda: _click_submit_if_present(page, replacing=replacing),
    )
    await page.wait_for_timeout(1500)
    await _dismiss_modals(page)
    if screenshot_dir:
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(screenshot_dir / f"{slug}.png"), full_page=True)
    return page
