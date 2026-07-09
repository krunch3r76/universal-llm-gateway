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
from claude_bundles.skills_ui_network import UploadNetworkOracle, UploadResult
from claude_bundles.skills_ui_panel import (
    NavigationGate,
    _dismiss_modals,
    _find_add_button,
    _is_skills_url,
    _js_click_upload_menuitem,
    _panel_lost_mid_attempt,
    _recover_panel_spa,
    _stability_guarded_add_click,
    _upload_modal_open,
    _upload_modal_root,
    panel_state_summary,
    slug_in_skills_table,
    snapshot_slug_row,
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


class UploadModalMissingError(RuntimeError):
    """Upload modal absent — refuse page-wide file input."""


async def _assert_upload_modal_scoped(page: Page) -> Locator:
    if not _is_skills_url(page.url):
        raise UploadModalMissingError(
            f"Not on skills panel URL — refusing file input ({page.url})"
        )
    root = await _upload_modal_root(page)
    if root is None:
        raise UploadModalMissingError(
            "Upload modal not open — refusing page-wide file input"
        )
    return root


async def _modal_file_input(page: Page) -> Locator:
    root = await _assert_upload_modal_scoped(page)
    inp = root.locator('input[type="file"]')
    if not await inp.count():
        raise UploadModalMissingError("Upload modal has no scoped file input")
    return inp.first


async def _modals_blocking(page: Page) -> bool:
    return await _upload_modal_open(page) or await replace_confirm_open(page)


async def _table_rows_text(page: Page) -> list[str]:
    rows = page.locator("table tbody tr")
    return [(await rows.nth(i).inner_text()).strip() for i in range(await rows.count())]


async def _upload_verified(
    page: Page,
    slug: str,
    rows_before: int,
    *,
    replacing: bool,
    replace_confirmed: bool,
    network: UploadResult | None,
    row_snapshot: str | None,
) -> bool:
    if network is not None and network.ok and network.slug_echoed:
        return True
    if network is not None and network.status and not (200 <= network.status < 300):
        return False

    if replace_confirmed and network is None:
        current = await snapshot_slug_row(page, slug)
        if current and row_snapshot and current != row_snapshot:
            return True
        if current:
            return True

    if await slug_in_skills_table(page, slug):
        if network is None and not replacing:
            return await page.locator("table tbody tr").count() > rows_before
        return False

    if replacing:
        return False
    if network is None:
        return await page.locator("table tbody tr").count() > rows_before
    return False


async def _open_upload_dialog(
    page: Page,
    context,
    *,
    nav_gate: NavigationGate | None,
) -> Locator:
    if await _upload_modal_open(page):
        return await _modal_file_input(page)

    await _dismiss_modals(page)
    add_btn = await _find_add_button(page)
    if not add_btn:
        raise RuntimeError(
            "Add button not found — open Customize → Skills (Browse + Add visible), then re-run"
        )

    last_err: Exception | None = None
    for attempt in range(5):
        try:
            add_btn = await _find_add_button(page)
            if add_btn is None:
                page = await _recover_panel_spa(page, context, nav_gate=nav_gate)
                add_btn = await _find_add_button(page)
                if add_btn is None:
                    raise RuntimeError("Add button not found after SPA panel recovery")

            try:
                await _stability_guarded_add_click(add_btn)
            except Exception as click_exc:
                print(f"OPEN_UPLOAD_DIALOG add-click failed: {click_exc!r}", file=sys.stderr)

            await page.wait_for_timeout(400 + 150 * attempt)

            if await _panel_lost_mid_attempt(page):
                page = await _recover_panel_spa(page, context, nav_gate=nav_gate)
                if await _find_add_button(page) is None:
                    raise RuntimeError("Panel not recovered after nav-away to /new")
                continue

            js_clicked = await _js_click_upload_menuitem(page, add_btn)
            if not js_clicked.get("ok"):
                upload_item = page.get_by_role("menuitem", name=_UPLOAD_MENU)
                if not await upload_item.count():
                    upload_item = page.locator("[role='menuitem']").filter(
                        has_text=_UPLOAD_MENU
                    )
                if not await upload_item.count():
                    raise RuntimeError("Add → Upload a skill menu item not found")
                try:
                    await upload_item.first.click(force=True, timeout=5_000)
                except Exception as click_exc:
                    print(f"OPEN_UPLOAD_DIALOG force-click failed: {click_exc!r}", file=sys.stderr)
                    item_handle = await upload_item.first.element_handle()
                    if item_handle is None:
                        raise
                    await page.evaluate("(el) => el.click()", item_handle)

            for _ in range(30):
                if await _upload_modal_open(page):
                    return await _modal_file_input(page)
                await page.wait_for_timeout(500)
            raise RuntimeError("Upload modal did not open within 15s")
        except Exception as exc:
            last_err = exc
            print(f"OPEN_UPLOAD_DIALOG attempt={attempt} err={exc!r}", file=sys.stderr)
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(400)
            if await _panel_lost_mid_attempt(page):
                page = await _recover_panel_spa(page, context, nav_gate=nav_gate)
            if await _find_add_button(page) is None:
                page = await _recover_panel_spa(page, context, nav_gate=nav_gate)
                if await _find_add_button(page) is None:
                    break

    summary = await panel_state_summary(page, context)
    raise RuntimeError(
        f"Add → Upload a skill failed after retries: {last_err}\n{summary}"
    )


async def _modal_error_text(page: Page) -> str | None:
    if not await _upload_modal_open(page):
        return None
    root = await _upload_modal_root(page)
    if root is None:
        return None
    text = await root.inner_text()
    if "Drag and drop or click to upload" in text and len(text) < 600:
        if not _REJECT_RE.search(text):
            return None
    if _REJECT_RE.search(text):
        return text[:400]
    return None


async def _select_file_in_modal(page: Page, skill_path: Path) -> None:
    root = await _assert_upload_modal_scoped(page)

    resolved = str(skill_path.resolve())
    drop = root.filter(has_text=_DROP_ZONE)
    if await drop.count():
        trigger = drop.first.get_by_text(_DROP_ZONE)
        if await trigger.count():
            root = await _assert_upload_modal_scoped(page)
            async with page.expect_file_chooser(timeout=15_000) as fc_info:
                await trigger.first.click()
            chooser = await fc_info.value
            root = await _assert_upload_modal_scoped(page)
            await chooser.set_files(resolved)
            return

    root = await _assert_upload_modal_scoped(page)
    inp = root.locator('input[type="file"]')
    if not await inp.count():
        raise UploadModalMissingError("Upload modal has no file input or drop zone")
    await inp.first.set_input_files(resolved)
    await inp.first.evaluate(
        """el => {
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }"""
    )


async def _wait_file_selected(page: Page, skill_path: Path) -> None:
    basename = skill_path.name
    stem = skill_path.stem
    for _ in range(40):
        if await replace_confirm_open(page):
            return
        root = await _upload_modal_root(page)
        if root is None:
            return
        text = await root.inner_text()
        if basename in text or stem in text:
            return
        if "Drag and drop or click to upload" not in text:
            return
        await page.wait_for_timeout(300)
    raise RuntimeError(
        f"File not accepted in upload modal ({basename}) — drop zone still empty"
    )


async def _click_upload_submit(page: Page) -> None:
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


def _network_dict(result: UploadResult | None) -> dict | None:
    if result is None:
        return None
    return {
        "ok": result.ok,
        "status": result.status,
        "slug_echoed": result.slug_echoed,
        "slug": result.slug,
        "skill_upload_url": result.skill_upload_url,
    }


async def _wait_upload_complete(
    page: Page,
    slug: str,
    *,
    replacing: bool = False,
    desc_len: int | None = None,
    rows_before: int = 0,
    retry_submit: object | None = None,
    replace_confirmed: bool = False,
    oracle: UploadNetworkOracle | None = None,
    row_snapshot: str | None = None,
) -> UploadResult:
    network = None
    if oracle is not None:
        network = await oracle.await_upload_result(slug, timeout_ms=30_000)

    await page.wait_for_timeout(1500)
    retried = False
    clear_ticks = 0
    for tick in range(120):
        await page.wait_for_timeout(500)
        if oracle is not None and network is not None and not network.ok:
            network = oracle.result_for(slug) or network
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
            page,
            slug,
            rows_before,
            replacing=replacing,
            replace_confirmed=replace_confirmed,
            network=network,
            row_snapshot=row_snapshot,
        )

        if not blocking:
            clear_ticks += 1
            if verified and clear_ticks >= 2:
                await _dismiss_modals(page)
                return network or UploadResult(
                    ok=False, status=0, slug_echoed=False, slug=slug
                )
            if clear_ticks >= 6 and not verified:
                rows = await _table_rows_text(page)
                net_log = oracle.captured_log() if oracle else []
                if network is None and rows and not await slug_in_skills_table(page, slug):
                    raise RuntimeError(
                        f"Upload did not verify for {slug} — no network signal, "
                        f"rows present, slug absent.\n"
                        f"table={rows!r}\nnetwork={net_log!r}"
                    )
                raise RuntimeError(
                    f"Upload did not verify for {slug} — modals closed but slug not confirmed.\n"
                    f"network={_network_dict(network)}\ntable={rows!r}"
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
        root = await _upload_modal_root(page)
        if root is not None:
            hint = (await root.inner_text())[:300]
    replace_hint = " — re-upload of table skill" if replacing else ""
    over = (
        f" (description {desc_len} chars > {MAX_CLAUDE_AI_DESCRIPTION_LEN})"
        if desc_len and desc_len > MAX_CLAUDE_AI_DESCRIPTION_LEN
        else ""
    )
    stage = "replace confirm" if await replace_confirm_open(page) else "upload modal"
    net_log = oracle.captured_log() if oracle else []
    raise RuntimeError(
        f"Upload timed out for {slug}{over}{replace_hint} ({stage} still open): {hint}\n"
        f"network={_network_dict(network)}\nlog={net_log!r}"
    )


async def upload_one_skill(
    page: Page,
    skill_path: Path,
    slug: str,
    *,
    replacing: bool = False,
    screenshot_dir: Path | None = None,
    desc_len: int | None = None,
    context=None,
    nav_gate: NavigationGate | None = None,
    oracle: UploadNetworkOracle | None = None,
) -> Page:
    rows_before = await page.locator("table tbody tr").count()
    row_snapshot = await snapshot_slug_row(page, slug) if replacing else None

    if oracle is not None:
        oracle.expect_slug(slug)
        oracle.attach()

    await _open_upload_dialog(page, context, nav_gate=nav_gate)
    await _select_file_in_modal(page, skill_path)
    await _wait_file_selected(page, skill_path)
    replace_confirmed = await _click_submit_if_present(page, replacing=replacing)

    network = await _wait_upload_complete(
        page,
        slug,
        replacing=replacing,
        desc_len=desc_len,
        rows_before=rows_before,
        replace_confirmed=replace_confirmed,
        retry_submit=lambda: _click_submit_if_present(page, replacing=replacing),
        oracle=oracle,
        row_snapshot=row_snapshot,
    )

    if network is not None and not (network.ok and network.slug_echoed):
        rows = await _table_rows_text(page)
        net_log = oracle.captured_log() if oracle else []
        raise RuntimeError(
            f"Upload network oracle failed for {slug}: {_network_dict(network)}\n"
            f"table={rows!r}\nnetwork={net_log!r}"
        )

    await page.wait_for_timeout(1500)
    await _dismiss_modals(page)
    if screenshot_dir:
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(screenshot_dir / f"{slug}.png"), full_page=True)
    return page
