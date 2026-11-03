"""Cowork/None surface pin — quoted inline must not satisfy shared_sync (a:41730)."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from claude_bundles.chat_context_skills import LoadedSkillsReport
from claude_bundles.cowork_skill_delivery import SkillReceiptUnverifiedError

pytestmark = pytest.mark.offline

_BODY_A = (
    "<!--cdp-required-skills:reasoning-posture-->\n"
    "# Review packet\n"
    "Quoted:\n"
    "<skills_inline>\n"
    '<skill slug="reasoning-posture" surface_class="shared_sync">\n'
    "quoted text\n"
    "</skill>\n"
    "</skills_inline>\n"
    "End.\n"
)


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", [None, "cowork"])
async def test_cowork_inline_pin_panel_observed_induction(surface: str | None) -> None:
    from claude_bundles.project_ask import send_prompt

    page = AsyncMock()
    composer = AsyncMock()
    submit_calls: list[str] = []
    report = LoadedSkillsReport(
        url="https://claude.ai/chat/x",
        skills=("reasoning-posture",),
        context_found=True,
        skills_heading_found=True,
        model_label=None,
        selectors=(),
        raw_section_text="",
    )

    async def _capture_submit(*_args, **kwargs):
        submit_calls.append(kwargs.get("draft_text") or "")

    patches = (
        patch("claude_bundles.composer_session_skills.require_compose_surface"),
        patch(
            "claude_bundles.project_ask.find_composer",
            new=AsyncMock(return_value=composer),
        ),
        patch(
            "claude_bundles.composer_submit.clear_composer_verified",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.project_ask._insert_prompt_text",
            new=AsyncMock(return_value=([], [])),
        ),
        patch(
            "claude_bundles.project_ask._submit_composer_draft",
            side_effect=_capture_submit,
        ),
        patch(
            "claude_bundles.skill_induction_panel.wait_for_induction_panel",
            new=AsyncMock(return_value=report),
        ),
        patch(
            "claude_bundles.skill_context_receipt.record_post_submit_skills_receipt",
            new=AsyncMock(),
        ),
        patch.object(page, "wait_for_timeout", new=AsyncMock()),
    )
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        await send_prompt(page, _BODY_A, claude_surface=surface)

    assert submit_calls
    draft = submit_calls[-1]
    assert draft.startswith("Use the reasoning-posture skill")
    assert "skill-seal:" not in draft


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", [None, "cowork"])
async def test_cowork_inline_pin_empty_panel_wrong_channel(surface: str | None) -> None:
    from claude_bundles.chat_context_skills import ChatContextSkillsError
    from claude_bundles.project_ask import send_prompt

    page = AsyncMock()
    composer = AsyncMock()

    patches = (
        patch("claude_bundles.composer_session_skills.require_compose_surface"),
        patch(
            "claude_bundles.project_ask.find_composer",
            new=AsyncMock(return_value=composer),
        ),
        patch(
            "claude_bundles.composer_submit.clear_composer_verified",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.project_ask._insert_prompt_text",
            new=AsyncMock(return_value=([], [])),
        ),
        patch(
            "claude_bundles.project_ask._submit_composer_draft",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.skill_induction_panel.wait_for_induction_panel",
            side_effect=ChatContextSkillsError("Context → Skills not on the rail"),
        ),
        patch(
            "claude_bundles.skill_context_receipt.record_post_submit_skills_receipt",
            new=AsyncMock(),
        ),
        patch.object(page, "wait_for_timeout", new=AsyncMock()),
    )
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        with pytest.raises(
            SkillReceiptUnverifiedError, match=r"wrong_channel=\['reasoning-posture'\]"
        ):
            await send_prompt(page, _BODY_A, claude_surface=surface)
