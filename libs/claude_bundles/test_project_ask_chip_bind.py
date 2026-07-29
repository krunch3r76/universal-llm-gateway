"""Tests for Cowork + → Skills session-skill attach wiring."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from claude_bundles.project_ask import _insert_prompt_text

pytestmark = pytest.mark.offline


def _composer_mock() -> AsyncMock:
    composer = AsyncMock()
    composer.click = AsyncMock()
    composer.type = AsyncMock()
    composer.press = AsyncMock()
    return composer


@pytest.mark.asyncio
async def test_insert_prompt_attaches_via_plus_menu_not_typing() -> None:
    page = AsyncMock()
    page.keyboard = AsyncMock()
    page.keyboard.insert_text = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    composer = _composer_mock()

    with patch(
        "claude_bundles.composer_session_skills.attach_session_skills",
        new_callable=AsyncMock,
        return_value=["cdp-operator-proxy", "reasoning-posture"],
    ) as attach:
        await _insert_prompt_text(
            page,
            "/cdp-operator-proxy\n/reasoning-posture\n\n# Sealed\n",
            composer=composer,
        )

    attach.assert_awaited_once()
    assert attach.await_args.args[1] == ["cdp-operator-proxy", "reasoning-posture"]
    composer.type.assert_not_awaited()
    page.keyboard.insert_text.assert_awaited_once_with("\n# Sealed\n")


@pytest.mark.asyncio
async def test_insert_prompt_continues_when_no_attach_slugs_requested() -> None:
    page = AsyncMock()
    page.keyboard = AsyncMock()
    page.keyboard.insert_text = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    composer = _composer_mock()

    with patch(
        "claude_bundles.composer_session_skills.attach_session_skills",
        new_callable=AsyncMock,
    ) as attach:
        await _insert_prompt_text(
            page,
            "<skills_inline><skill slug=\"path-sim\" surface_class=\"cursor_only\">"
            "body</skill></skills_inline>\n\n# Sealed\n",
            composer=composer,
        )

    attach.assert_not_awaited()
    page.keyboard.insert_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_insert_prompt_aborts_when_required_attach_slugs_undelivered() -> None:
    from claude_bundles.cowork_skill_delivery import SkillDeliveryError

    page = AsyncMock()
    page.keyboard = AsyncMock()
    page.keyboard.insert_text = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    composer = _composer_mock()

    with patch(
        "claude_bundles.composer_session_skills.attach_session_skills",
        new_callable=AsyncMock,
        return_value=[],
    ):
        with pytest.raises(SkillDeliveryError, match="undelivered"):
            await _insert_prompt_text(
                page,
                "/reasoning-posture\n\n# Sealed\n",
                composer=composer,
            )

    page.keyboard.insert_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_insert_prompt_skips_non_claude_slash_for_attach() -> None:
    page = AsyncMock()
    page.keyboard = AsyncMock()
    page.keyboard.insert_text = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    composer = _composer_mock()

    with patch(
        "claude_bundles.composer_session_skills.attach_session_skills",
        new_callable=AsyncMock,
        return_value=["cdp-operator-proxy"],
    ) as attach:
        await _insert_prompt_text(
            page,
            "/path-sim\n/cdp-operator-proxy\n\n# Sealed\n",
            composer=composer,
        )

    attach.assert_awaited_once_with(page, ["cdp-operator-proxy"], composer=composer)
    page.keyboard.insert_text.assert_awaited_once_with("\n# Sealed\n")
