"""Tests for claude.ai Skills upload harden (D1/D2/D3)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_bundles.skills_ui_evidence import composer_has_attachments
from claude_bundles.skills_ui_network import (
    UploadResult,
    _is_noise_url,
    _is_skills_upload,
    _slug_in_text,
)
from claude_bundles.skills_ui_upload import (
    UploadModalMissingError,
    _modal_file_input,
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
        "claude_bundles.skills_ui_upload._upload_modal_root",
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
        "claude_bundles.skills_ui_upload._upload_modal_root",
        new_callable=AsyncMock,
        return_value=None,
    ):
        with pytest.raises(UploadModalMissingError, match="Upload modal not open"):
            await _select_file_in_modal(page, Path("/tmp/fake-skill.md"))


@pytest.mark.asyncio
async def test_modal_file_input_raises_when_not_on_skills_url() -> None:
    page = AsyncMock()
    page.url = "https://claude.ai/new"
    with pytest.raises(UploadModalMissingError, match="Not on skills panel URL"):
        await _modal_file_input(page)


@pytest.mark.asyncio
async def test_stability_guarded_add_click_skips_when_expanded() -> None:
    from claude_bundles.skills_ui_menu import stability_guarded_add_click

    add_btn = AsyncMock()
    add_btn.get_attribute = AsyncMock(return_value="true")
    add_btn.scroll_into_view_if_needed = AsyncMock()
    add_btn.wait_for = AsyncMock()
    add_btn.click = AsyncMock()
    await stability_guarded_add_click(add_btn)
    add_btn.click.assert_not_called()


def _mock_locator(*, count: int = 0, visible: bool = False) -> MagicMock:
    loc = MagicMock()
    loc.count = AsyncMock(return_value=count)
    nth = MagicMock()
    nth.is_visible = AsyncMock(return_value=visible)
    loc.nth = MagicMock(return_value=nth)
    return loc


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
async def test_stability_guarded_add_click_clicks_when_closed() -> None:
    from claude_bundles.skills_ui_menu import stability_guarded_add_click

    add_btn = AsyncMock()
    add_btn.get_attribute = AsyncMock(return_value="false")
    add_btn.scroll_into_view_if_needed = AsyncMock()
    add_btn.wait_for = AsyncMock()
    add_btn.click = AsyncMock()
    await stability_guarded_add_click(add_btn)
    add_btn.click.assert_awaited_once()
