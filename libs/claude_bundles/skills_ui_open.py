"""Open the claude.ai Skills Add → Upload dialog and scope the file input.

Callers: ``upload_one_skill`` via ``_open_upload_dialog``. Retry cap is 5 —
the same loop as before inventory-first discovery; no extra sleep/retry layer.
"""

from __future__ import annotations

import sys

from playwright.async_api import Locator, Page

from claude_bundles.skills_ui_menu import (
    MenuDiscoveryError,
    MenuInventory,
    add_menu_expanded,
    js_click_menuitem_at,
    resolve_upload_selection,
    stability_guarded_add_click,
    wait_menu_idle,
)
from claude_bundles.skills_ui_panel import (
    NavigationGate,
    _dismiss_modals,
    _find_add_button,
    _is_skills_url,
    _panel_lost_mid_attempt,
    _recover_panel_spa,
    _upload_modal_open,
    _upload_modal_root,
    panel_state_summary,
)


class UploadModalMissingError(RuntimeError):
    """Upload modal absent or off the Skills URL — refuse a page-wide file chooser."""


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


async def _open_upload_dialog(
    page: Page,
    context,
    *,
    nav_gate: NavigationGate | None,
) -> Locator:
    """Click Add → Upload (inventory select) and return the modal file input."""
    if await _upload_modal_open(page):
        return await _modal_file_input(page)

    await _dismiss_modals(page)
    add_btn = await _find_add_button(page)
    if not add_btn:
        raise RuntimeError(
            "Add button not found — open Customize → Skills (Browse + Add visible), then re-run"
        )

    last_err: Exception | None = None
    last_inv: MenuInventory | None = None
    for attempt in range(5):
        try:
            add_btn = await _find_add_button(page)
            if add_btn is None:
                page = await _recover_panel_spa(page, context, nav_gate=nav_gate)
                add_btn = await _find_add_button(page)
                if add_btn is None:
                    raise RuntimeError("Add button not found after SPA panel recovery")

            try:
                await stability_guarded_add_click(add_btn)
            except Exception as click_exc:
                # Menu often already open (aria-expanded) while locator is unstable —
                # proceed if expanded; only fail when menu is closed.
                add_btn = await _find_add_button(page) or add_btn
                if not await add_menu_expanded(add_btn):
                    print(
                        f"OPEN_UPLOAD_DIALOG add-click failed: {click_exc!r}",
                        file=sys.stderr,
                    )
                    raise

            if await _panel_lost_mid_attempt(page):
                page = await _recover_panel_spa(page, context, nav_gate=nav_gate)
                if await _find_add_button(page) is None:
                    raise RuntimeError("Panel not recovered after nav-away to /new")
                continue

            inv = await wait_menu_idle(
                page, add_btn, timeout_ms=2_000 + 500 * attempt
            )
            last_inv = inv
            sel, inv = await resolve_upload_selection(page, add_btn, inv)
            last_inv = inv
            if sel.status not in ("found", "drift") or sel.index is None:
                raise MenuDiscoveryError(
                    "Add → Upload a skill menu item not found", inv
                )
            clicked = await js_click_menuitem_at(page, add_btn, sel.index)
            if not clicked.get("ok"):
                raise MenuDiscoveryError(
                    "Add → Upload a skill menu item not found", inv
                )

            for _ in range(30):
                if await _upload_modal_open(page):
                    return await _modal_file_input(page)
                await page.wait_for_timeout(500)
            raise RuntimeError("Upload modal did not open within 15s")
        except Exception as exc:
            last_err = exc
            if isinstance(exc, MenuDiscoveryError):
                last_inv = exc.inventory
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
    message = f"Add → Upload a skill failed after retries: {last_err}\n{summary}"
    if last_inv is not None:
        raise MenuDiscoveryError(message, last_inv)
    raise RuntimeError(message)
