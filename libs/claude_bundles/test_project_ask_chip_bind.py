"""Tests for Cowork + → Skills session-skill attach wiring.

``_insert_prompt_text`` no longer trusts the clicker's return list — it drives
``attach_skills_verified``, which re-reads the composer chips. The page fake
therefore has to serve chip payloads, one per observation round.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from claude_bundles.project_ask import _insert_prompt_text

pytestmark = pytest.mark.offline

ATTACH_ONE = "claude_bundles.composer_session_skills.attach_one_session_skill"


def _composer_mock() -> AsyncMock:
    composer = AsyncMock()
    composer.click = AsyncMock()
    composer.type = AsyncMock()
    composer.press = AsyncMock()
    return composer


def _page_mock(chip_rounds: list[list[str]]) -> AsyncMock:
    """Page whose ``evaluate`` returns one chip payload per round; last repeats."""
    page = AsyncMock()
    page.url = "https://claude.ai/new"
    page.keyboard = AsyncMock()
    page.keyboard.insert_text = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    rounds = [list(r) for r in chip_rounds] or [[]]

    async def _evaluate(*_args, **_kwargs):
        slugs = rounds.pop(0) if len(rounds) > 1 else rounds[0]
        return {"ok": True, "slugs": slugs}

    page.evaluate = _evaluate
    return page


def _attached_slugs(attach: AsyncMock) -> list[str]:
    return [call.args[1] for call in attach.await_args_list]


@pytest.mark.asyncio
async def test_insert_prompt_attaches_via_plus_menu_not_typing() -> None:
    page = _page_mock([[], ["cdp-operator-proxy", "reasoning-posture"]])
    composer = _composer_mock()

    with patch(ATTACH_ONE, new_callable=AsyncMock) as attach:
        await _insert_prompt_text(
            page,
            "/cdp-operator-proxy\n/reasoning-posture\n\n# Sealed\n",
            composer=composer,
        )

    assert _attached_slugs(attach) == ["cdp-operator-proxy", "reasoning-posture"]
    composer.type.assert_not_awaited()
    page.keyboard.insert_text.assert_awaited_once_with("\n# Sealed\n")


@pytest.mark.asyncio
async def test_insert_prompt_continues_when_no_attach_slugs_requested() -> None:
    page = _page_mock([[]])
    composer = _composer_mock()

    with patch(ATTACH_ONE, new_callable=AsyncMock) as attach:
        await _insert_prompt_text(
            page,
            '<skills_inline><skill slug="path-sim" surface_class="cursor_only">'
            "body</skill></skills_inline>\n\n# Sealed\n",
            composer=composer,
        )

    attach.assert_not_awaited()
    page.keyboard.insert_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_insert_prompt_aborts_when_required_attach_slugs_undelivered() -> None:
    from claude_bundles.cowork_skill_delivery import SkillDeliveryError

    page = _page_mock([[]])
    composer = _composer_mock()

    with patch(ATTACH_ONE, new_callable=AsyncMock):
        with pytest.raises(SkillDeliveryError, match="undelivered"):
            await _insert_prompt_text(
                page,
                "/reasoning-posture\n\n# Sealed\n",
                composer=composer,
            )

    page.keyboard.insert_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_insert_prompt_aborts_when_clicker_succeeds_but_no_chip_lands() -> None:
    """The a25806 regression: a clean click return is not a landed skill."""
    from claude_bundles.cowork_skill_delivery import SkillDeliveryError

    page = _page_mock([[]])
    composer = _composer_mock()

    with patch(ATTACH_ONE, new_callable=AsyncMock) as attach:
        with pytest.raises(SkillDeliveryError, match="undelivered"):
            await _insert_prompt_text(
                page,
                "/cdp-operator-proxy\n/reasoning-posture\n\n# Sealed\n",
                composer=composer,
            )

    assert set(_attached_slugs(attach)) == {"cdp-operator-proxy", "reasoning-posture"}
    page.keyboard.insert_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_insert_prompt_skips_non_claude_slash_for_attach() -> None:
    page = _page_mock([[], ["cdp-operator-proxy"]])
    composer = _composer_mock()

    with patch(ATTACH_ONE, new_callable=AsyncMock) as attach:
        await _insert_prompt_text(
            page,
            "/path-sim\n/cdp-operator-proxy\n\n# Sealed\n",
            composer=composer,
        )

    assert _attached_slugs(attach) == ["cdp-operator-proxy"]
    page.keyboard.insert_text.assert_awaited_once_with("\n# Sealed\n")
