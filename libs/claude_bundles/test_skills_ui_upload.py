"""Tests for claude.ai Skills upload harden (D1/D2/D3)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_bundles.skills_ui_evidence import composer_has_attachments
from claude_bundles.skills_ui_landed import (
    confirm_skill_upload_ui,
    toast_names_slug,
)
from claude_bundles.skills_ui_menu import (
    MenuInventory,
    MenuItem,
    MenuPopup,
    MenuTrigger,
    UploadSelection,
)
from claude_bundles.skills_ui_network import (
    UploadResult,
    _is_noise_url,
    _is_skills_upload,
    _slug_in_text,
)
from claude_bundles.skills_ui_open import (
    UploadModalMissingError,
    _modal_file_input,
    _open_upload_dialog,
)
from claude_bundles.skills_ui_panel import (
    _upload_modal_open,
    _upload_modal_root,
    probe_upload_modal_mismatch,
)
from claude_bundles.skills_ui_upload import (
    _select_file_in_modal,
)

_F9_NOISE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tmp"
    / "claude-ai-live-verify-20260709"
    / "f9-false-positive-hits.json"
)

_F9_NOISE_FALLBACK = [
    {
        "status": 200,
        "url": "https://a-api.anthropic.com/v1/b",
        "body": '{\n  "success": true\n}',
    },
    {
        "status": 202,
        "url": "https://browser-intake-us5-datadoghq.com/api/v2/rum?ddsource=browser",
        "body": '{"request_id":"8dd7fb1f-6610-4f1f-bde1-8a10a57c84c4"}',
    },
    {
        "status": 200,
        "url": "https://a-api.anthropic.com/v1/m",
        "body": '{\n  "success": true\n}',
    },
    {
        "status": 200,
        "url": "https://claude.ai/api/event_logging/v2/batch",
        "body": '{"accepted_count":23,"rejected_count":0}',
    },
]


def _load_f9_noise() -> list[dict]:
    if _F9_NOISE_PATH.is_file():
        return json.loads(_F9_NOISE_PATH.read_text(encoding="utf-8"))
    return _F9_NOISE_FALLBACK


def _oracle_outcome(url: str, status: int, body: str, slug: str) -> UploadResult:
    post_data = ""
    slug_echoed = _slug_in_text(post_data, slug) or _slug_in_text(body, slug)
    is_candidate = _is_skills_upload(url, "POST", post_data, body, slug)
    ok = is_candidate and 200 <= status < 300 and slug_echoed
    return UploadResult(
        ok=ok,
        status=status,
        slug_echoed=slug_echoed and is_candidate,
        slug=slug,
    )


def test_noise_urls_rejected_by_predicate() -> None:
    for entry in _load_f9_noise():
        assert _is_noise_url(entry["url"]), entry["url"]
        assert not _is_skills_upload(
            entry["url"],
            "POST",
            "",
            entry.get("body", ""),
            "cheap-recon",
        )


def test_f9_noise_set_yields_no_oracle_success() -> None:
    slug = "cheap-recon"
    for entry in _load_f9_noise():
        result = _oracle_outcome(entry["url"], entry["status"], entry.get("body", ""), slug)
        assert not result.ok, entry["url"]
        assert not result.slug_echoed, entry["url"]


def test_bare_2xx_without_slug_echo_is_not_ok() -> None:
    url = "https://claude.ai/api/organizations/org-1/skills"
    body = '{"accepted": true}'
    result = _oracle_outcome(url, 202, body, "cheap-recon")
    assert not result.slug_echoed
    assert not result.ok


def test_positive_skills_upload_with_slug_in_body() -> None:
    slug = "test-skill"
    url = "https://claude.ai/api/organizations/org-1/skills"
    body = json.dumps({"slug": slug, "ok": True})
    assert _is_skills_upload(url, "POST", "", body, slug)
    result = _oracle_outcome(url, 200, body, slug)
    assert result.slug_echoed
    assert result.ok


@pytest.mark.asyncio
async def test_modal_file_input_raises_when_modal_absent() -> None:
    page = AsyncMock()
    page.url = "https://claude.ai/new#settings/customize-skills"
    with patch(
        "claude_bundles.skills_ui_open._upload_modal_root",
        new_callable=AsyncMock,
        return_value=None,
    ):
        with pytest.raises(UploadModalMissingError, match="Upload modal not open"):
            await _modal_file_input(page)


@pytest.mark.asyncio
async def test_select_file_in_modal_raises_when_modal_absent() -> None:
    page = AsyncMock()
    page.url = "https://claude.ai/new#settings/customize-skills"
    with patch(
        "claude_bundles.skills_ui_open._upload_modal_root",
        new_callable=AsyncMock,
        return_value=None,
    ):
        with pytest.raises(UploadModalMissingError, match="Upload modal not open"):
            await _select_file_in_modal(page, Path("/tmp/fake-skill.md"))


def _mock_upload_overlay(
    inner_text: str,
    *,
    file_input_count: int = 1,
    visible: bool = True,
) -> MagicMock:
    file_inp = _mock_locator(count=file_input_count)
    ov = MagicMock()
    ov.is_visible = AsyncMock(return_value=visible)
    ov.inner_text = AsyncMock(return_value=inner_text)

    def _locator(sel: str) -> MagicMock:
        if sel == 'input[type="file"]':
            return file_inp
        return _mock_locator()

    ov.locator = MagicMock(side_effect=_locator)
    return ov


def _mock_page_with_upload_overlay(inner_text: str, *, file_input_count: int = 1) -> MagicMock:
    overlay = _mock_upload_overlay(inner_text, file_input_count=file_input_count)
    overlays = MagicMock()
    overlays.count = AsyncMock(return_value=1)
    overlays.nth = MagicMock(return_value=overlay)
    title = _mock_locator(count=0)
    page = MagicMock()
    page.url = "https://claude.ai/new#settings/customize-skills"
    page.get_by_text = MagicMock(return_value=title)

    def _locator(sel: str) -> MagicMock:
        if "data-popup-open" in sel or "role=\"dialog\"" in sel:
            return overlays
        return _mock_locator()

    page.locator = MagicMock(side_effect=_locator)
    return page


@pytest.mark.asyncio
async def test_upload_modal_detects_drop_zone_only_overlay() -> None:
    page = _mock_page_with_upload_overlay("Drag and drop or click to upload")
    assert await _upload_modal_open(page)
    root = await _upload_modal_root(page)
    assert root is not None
    inp = root.locator('input[type="file"]')
    assert await inp.count() == 1


@pytest.mark.asyncio
async def test_upload_modal_drop_zone_without_file_input_is_not_open() -> None:
    page = _mock_page_with_upload_overlay(
        "Drag and drop or click to upload", file_input_count=0
    )
    assert not await _upload_modal_open(page)


@pytest.mark.asyncio
async def test_probe_upload_modal_mismatch_text_outside_overlay_shell() -> None:
    """Drop-zone copy on page without a popup/dialog ancestor."""
    loose = MagicMock()
    loose.count = AsyncMock(return_value=1)
    loose.first.is_visible = AsyncMock(return_value=True)
    loose.first.locator = MagicMock(return_value=_mock_locator(count=0))
    empty_overlays = MagicMock()
    empty_overlays.count = AsyncMock(return_value=0)
    page = MagicMock()
    page.url = "https://claude.ai/new#settings/customize-skills"
    page.get_by_text = MagicMock(return_value=loose)

    def _locator(sel: str) -> MagicMock:
        if "data-popup-open" in sel or 'role="dialog"' in sel:
            return empty_overlays
        return _mock_locator()

    page.locator = MagicMock(side_effect=_locator)

    probe = await probe_upload_modal_mismatch(page)
    assert probe["text_loose_or_strict_match"] is True
    assert probe["overlay_shell"] is False
    assert probe["drop_zone_text"] is True
    assert not await _upload_modal_open(page)


@pytest.mark.asyncio
async def test_probe_upload_modal_mismatch_drop_zone_overlay_no_file_input() -> None:
    page = _mock_page_with_upload_overlay(
        "Drag and drop or click to upload", file_input_count=0
    )
    probe = await probe_upload_modal_mismatch(page)
    assert probe["drop_zone_text"] is True
    assert probe["file_input_in_overlay"] is False
    assert probe["overlay_shell"] is True
    assert not await _upload_modal_open(page)


@pytest.mark.asyncio
async def test_modal_file_input_resolves_scoped_input_on_drop_zone_overlay() -> None:
    page = _mock_page_with_upload_overlay("Drag and drop or click to upload")
    with patch(
        "claude_bundles.skills_ui_open._upload_modal_root",
        new_callable=AsyncMock,
        side_effect=lambda _p: _mock_upload_overlay("Drag and drop or click to upload"),
    ):
        inp = await _modal_file_input(page)
    assert inp is not None


@pytest.mark.asyncio
async def test_open_upload_dialog_succeeds_when_drop_zone_modal_detected() -> None:
    page = MagicMock()
    _wire_page_playwright_stubs(page)
    page.url = "https://claude.ai/new#settings/customize-skills"
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    add_btn = AsyncMock()
    inv = _menu_inventory(page.url)
    sel = UploadSelection(status="found", index=0, text="Upload a skill")
    file_inp = _mock_locator(count=1)
    file_root = _mock_upload_overlay("Drag and drop or click to upload")
    file_root.locator = MagicMock(return_value=file_inp)

    open_flags = iter([False, True])

    async def _modal_open(_page) -> bool:
        return next(open_flags, True)

    with (
        patch(
            "claude_bundles.skills_ui_open._find_add_button",
            new_callable=AsyncMock,
            return_value=add_btn,
        ),
        patch("claude_bundles.skills_ui_open._dismiss_modals", new_callable=AsyncMock),
        patch(
            "claude_bundles.skills_ui_open.stability_guarded_add_click",
            new_callable=AsyncMock,
        ),
        patch(
            "claude_bundles.skills_ui_open.wait_menu_idle",
            new_callable=AsyncMock,
            return_value=inv,
        ),
        patch(
            "claude_bundles.skills_ui_open.resolve_upload_selection",
            new_callable=AsyncMock,
            return_value=(sel, inv),
        ),
        patch(
            "claude_bundles.skills_ui_open.js_click_menuitem_at",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ),
        patch(
            "claude_bundles.skills_ui_open._panel_lost_mid_attempt",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "claude_bundles.skills_ui_open._upload_modal_open",
            side_effect=_modal_open,
        ),
        patch(
            "claude_bundles.skills_ui_open._upload_modal_root",
            new_callable=AsyncMock,
            return_value=file_root,
        ),
    ):
        got = await _open_upload_dialog(page, MagicMock(), nav_gate=None)
    assert got is file_inp.first


@pytest.mark.asyncio
async def test_open_upload_dialog_modal_timeout_is_upload_modal_missing() -> None:
    page = MagicMock()
    _wire_page_playwright_stubs(page)
    page.url = "https://claude.ai/new#settings/customize-skills"
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    add_btn = AsyncMock()
    inv = _menu_inventory(page.url)
    sel = UploadSelection(status="found", index=0, text="Upload a skill")

    with (
        patch(
            "claude_bundles.skills_ui_open._find_add_button",
            new_callable=AsyncMock,
            return_value=add_btn,
        ),
        patch("claude_bundles.skills_ui_open._dismiss_modals", new_callable=AsyncMock),
        patch(
            "claude_bundles.skills_ui_open.stability_guarded_add_click",
            new_callable=AsyncMock,
        ),
        patch(
            "claude_bundles.skills_ui_open.wait_menu_idle",
            new_callable=AsyncMock,
            return_value=inv,
        ),
        patch(
            "claude_bundles.skills_ui_open.resolve_upload_selection",
            new_callable=AsyncMock,
            return_value=(sel, inv),
        ),
        patch(
            "claude_bundles.skills_ui_open.js_click_menuitem_at",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ),
        patch(
            "claude_bundles.skills_ui_open._panel_lost_mid_attempt",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "claude_bundles.skills_ui_open._upload_modal_open",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "claude_bundles.skills_ui_open.probe_upload_modal_mismatch",
            new_callable=AsyncMock,
            return_value={
                "overlay_shell": False,
                "text_loose_or_strict_match": True,
                "drop_zone_text": True,
                "file_input_in_overlay": False,
            },
        ),
        patch(
            "claude_bundles.skills_ui_open.panel_state_summary",
            new_callable=AsyncMock,
            return_value="",
        ),
        pytest.raises(UploadModalMissingError, match="did not open within 15s") as exc_info,
    ):
        await _open_upload_dialog(page, MagicMock(), nav_gate=None)
    probe = exc_info.value.probe
    assert set(probe) == {
        "overlay_shell",
        "text_loose_or_strict_match",
        "drop_zone_text",
        "file_input_in_overlay",
    }


@pytest.mark.asyncio
async def test_modal_file_input_raises_when_not_on_skills_url() -> None:
    page = AsyncMock()
    page.url = "https://claude.ai/new"
    with pytest.raises(UploadModalMissingError, match="Not on skills panel URL"):
        await _modal_file_input(page)


def _add_btn_stub(*, expanded: str = "false") -> AsyncMock:
    add_btn = AsyncMock()
    add_btn.page = AsyncMock()
    add_btn.get_attribute = AsyncMock(return_value=expanded)
    add_btn.scroll_into_view_if_needed = AsyncMock()
    add_btn.wait_for = AsyncMock()
    add_btn.click = AsyncMock()
    return add_btn


@pytest.mark.asyncio
async def test_stability_guarded_add_click_skips_when_expanded() -> None:
    from claude_bundles.skills_ui_menu import stability_guarded_add_click

    add_btn = _add_btn_stub(expanded="true")
    await stability_guarded_add_click(add_btn)
    add_btn.click.assert_not_called()


def _menu_inventory(url: str) -> MenuInventory:
    return MenuInventory(
        url=url,
        trigger=MenuTrigger(found=True, aria_expanded="true"),
        popup=MenuPopup(found_by="test", mounted=True, visible=True, menuitem_count=1),
        items=[
            MenuItem(
                index=0,
                text="Upload a skill",
                role="menuitem",
                aria_haspopup="",
                aria_disabled="",
                visible=True,
                id="",
            )
        ],
    )


def _mock_locator(*, count: int = 0, visible: bool = False) -> MagicMock:
    loc = MagicMock()
    loc.count = AsyncMock(return_value=count)
    nth = MagicMock()
    nth.is_visible = AsyncMock(return_value=visible)
    loc.nth = MagicMock(return_value=nth)
    loc.first = nth if count else loc
    return loc


def _wire_page_playwright_stubs(page: MagicMock) -> None:
    """Panel helpers await locator.count(); bare MagicMock page must mimic Playwright."""

    def _locator(_sel: str) -> MagicMock:
        loc = _mock_locator()
        loc.filter = MagicMock(return_value=_mock_locator())
        return loc

    page.get_by_role = MagicMock(side_effect=lambda *_a, **_k: _mock_locator())
    page.locator = MagicMock(side_effect=_locator)


def _mock_page(locator_map: dict[str, MagicMock]) -> MagicMock:
    page = MagicMock()

    def _locator(sel: str) -> MagicMock:
        return locator_map.get(sel, _mock_locator())

    page.locator = MagicMock(side_effect=_locator)
    return page


@pytest.mark.asyncio
async def test_composer_has_attachments_ignores_labs_beta_generic_chip() -> None:
    """Generic page-chrome chips must not trigger pollution (Labs/Beta false positive)."""
    generic_chip = _mock_locator(count=1, visible=True)
    page = _mock_page(
        {
            "[data-testid='file-attachment']": _mock_locator(),
            "[data-testid='attachment']": _mock_locator(),
            ".attachment-chip": _mock_locator(),
            "[class*='attachment']": _mock_locator(),
            "[class*='Attachment']": _mock_locator(),
            "[class*='chip'], [class*='Chip'], [data-testid*='chip']": generic_chip,
        }
    )
    assert not await composer_has_attachments(page)


@pytest.mark.asyncio
async def test_composer_has_attachments_detects_file_attachment_chip() -> None:
    """Realistic attachment DOM (file-attachment testid) must trigger pollution."""
    attachment = _mock_locator(count=1, visible=True)
    page = _mock_page(
        {
            "[data-testid='file-attachment']": attachment,
        }
    )
    assert await composer_has_attachments(page)


@pytest.mark.asyncio
async def test_composer_has_attachments_ignores_replaced_toast() -> None:
    """Success toast is not a composer chip — 2026-09-17 false fail."""
    composer = _mock_locator(count=1, visible=True)
    composer.locator = MagicMock(return_value=_mock_locator())
    toast = _mock_locator(count=1, visible=True)
    page = _mock_page(
        {
            "[data-testid='chat-input']": composer,
            "[class*='attachment']": toast,
            "[class*='Attachment']": toast,
        }
    )
    assert not await composer_has_attachments(page)


def test_toast_names_slug_matches_replaced_copy() -> None:
    assert toast_names_slug("Replaced retrieval-before-authoring", "retrieval-before-authoring")
    assert not toast_names_slug("Labs beta attachment", "retrieval-before-authoring")


@pytest.mark.asyncio
async def test_confirm_skill_upload_ui_toast_and_table() -> None:
    page = MagicMock()
    with (
        patch(
            "claude_bundles.skills_ui_landed.read_skill_upload_toast",
            new_callable=AsyncMock,
            return_value="Replaced retrieval-before-authoring",
        ),
        patch(
            "claude_bundles.skills_ui_landed.snapshot_slug_row",
            new_callable=AsyncMock,
            return_value="retrieval-before-authoring\t9/17/26\tYou",
        ),
    ):
        got = await confirm_skill_upload_ui(
            page, "retrieval-before-authoring", replacing=True
        )
    assert got.kind == "toast+table"
    assert got.toast == "Replaced retrieval-before-authoring"


@pytest.mark.asyncio
async def test_confirm_skill_upload_ui_raises_without_toast_or_row() -> None:
    page = MagicMock()
    with (
        patch(
            "claude_bundles.skills_ui_landed.read_skill_upload_toast",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "claude_bundles.skills_ui_landed.snapshot_slug_row",
            new_callable=AsyncMock,
            return_value=None,
        ),
        pytest.raises(RuntimeError, match="UI confirm missing"),
    ):
        await confirm_skill_upload_ui(page, "retrieval-before-authoring", replacing=True)


@pytest.mark.asyncio
async def test_stability_guarded_add_click_clicks_when_closed() -> None:
    from claude_bundles.skills_ui_menu import stability_guarded_add_click

    add_btn = _add_btn_stub(expanded="false")
    with (
        patch(
            "claude_bundles.skills_ui_menu.dismiss_base_ui_inert_portal",
            new_callable=AsyncMock,
        ) as dismiss,
        patch(
            "claude_bundles.skills_ui_menu.base_ui_inert_portal_blocks",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        await stability_guarded_add_click(add_btn)
    dismiss.assert_awaited_once_with(add_btn.page)
    add_btn.click.assert_awaited_once_with(timeout=3_000)


@pytest.mark.asyncio
async def test_stability_guarded_add_click_force_when_inert_portal_remains() -> None:
    from claude_bundles.skills_ui_menu import stability_guarded_add_click

    add_btn = _add_btn_stub(expanded="false")
    with (
        patch(
            "claude_bundles.skills_ui_menu.dismiss_base_ui_inert_portal",
            new_callable=AsyncMock,
        ) as dismiss,
        patch(
            "claude_bundles.skills_ui_menu.base_ui_inert_portal_blocks",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        await stability_guarded_add_click(add_btn)
    dismiss.assert_awaited_once_with(add_btn.page)
    add_btn.click.assert_awaited_once_with(timeout=3_000, force=True)
