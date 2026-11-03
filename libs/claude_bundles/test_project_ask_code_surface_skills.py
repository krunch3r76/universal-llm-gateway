"""Decisive /code surface skill delivery tests (friction a:41730)."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from claude_bundles.chat_context_skills import ChatContextSkillsError
from claude_bundles.cowork_skill_delivery import SkillDeliveryError

pytestmark = pytest.mark.offline

_MARKED = "<!--cdp-required-skills:reasoning-posture-->\n# Work body\n"


def _code_surface_harness(monkeypatch: pytest.MonkeyPatch):
    page = AsyncMock()
    composer = AsyncMock()
    submit_calls: list[str] = []
    panel_calls: list[object] = []
    attest_payloads: list[dict] = []

    async def _capture_submit(*_args, **kwargs):
        submit_calls.append(kwargs.get("draft_text") or "")

    async def _panel(*_args, **_kwargs):
        panel_calls.append(True)
        raise ChatContextSkillsError("Context → Skills not on the rail")

    def _capture_attest(**payload):
        attest_payloads.append(dict(payload))

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
            "claude_bundles.composer_skill_observe.attach_skills_verified",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "claude_bundles.project_ask._submit_composer_draft",
            side_effect=_capture_submit,
        ),
        patch(
            "claude_bundles.skill_induction_panel.wait_for_induction_panel",
            side_effect=_panel,
        ),
        patch(
            "claude_bundles.skill_context_receipt.record_post_submit_skills_receipt",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.cowork_skill_delivery.emit_skill_delivery_attested",
            side_effect=_capture_attest,
        ),
    )
    return page, submit_calls, panel_calls, attest_payloads, patches


@pytest.mark.asyncio
async def test_code_surface_marked_prompt_attests_via_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T3 — /code marked prompt inlines shared_sync and attests via inline."""
    from claude_bundles.project_ask import send_prompt

    page, submit_calls, panel_calls, attest_payloads, patches = _code_surface_harness(
        monkeypatch
    )
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        await send_prompt(page, _MARKED, claude_surface="code")

    assert submit_calls
    draft = submit_calls[-1]
    assert '<skill slug="reasoning-posture"' in draft
    assert "Use the reasoning-posture skill" not in draft
    assert panel_calls == []
    assert attest_payloads
    last = attest_payloads[-1]
    assert last["ok"] is True
    assert last["inlined"] == ["reasoning-posture"]
    assert last["induction"] == []
    rows = last["rows"]
    assert rows == [{"slug": "reasoning-posture", "delivered_via": "inline"}]


@pytest.mark.asyncio
async def test_code_surface_missing_sot_fails_before_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T4 — missing SOT raises SkillDeliveryError before submit."""
    from claude_bundles.project_ask import send_prompt

    page, submit_calls, _panel, _attest, patches = _code_surface_harness(monkeypatch)
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        stack.enter_context(
            patch(
                "claude_bundles.cowork_skill_delivery.load_skill_bodies",
                side_effect=SkillDeliveryError("no local SOT for inject: ['x']"),
            )
        )
        with pytest.raises(SkillDeliveryError, match="no local SOT"):
            await send_prompt(page, _MARKED, claude_surface="code")
    assert submit_calls == []


@pytest.mark.asyncio
async def test_cowork_surface_marked_prompt_keeps_induction_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T5 — non-code surface keeps Use-lines and panel wait."""
    from claude_bundles.chat_context_skills import LoadedSkillsReport
    from claude_bundles.project_ask import send_prompt

    page = AsyncMock()
    composer = AsyncMock()
    submit_calls: list[str] = []
    panel_calls: list[object] = []

    async def _capture_submit(*_args, **kwargs):
        submit_calls.append(kwargs.get("draft_text") or "")

    report = LoadedSkillsReport(
        url="https://claude.ai/chat/x",
        skills=("reasoning-posture",),
        context_found=True,
        skills_heading_found=True,
        model_label=None,
        selectors=(),
        raw_section_text="",
    )

    with (
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
        ) as panel_mock,
        patch(
            "claude_bundles.skill_context_receipt.record_post_submit_skills_receipt",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.cowork_skill_delivery.attest_delivery_channels",
            return_value=["reasoning-posture"],
        ),
    ):
        await send_prompt(page, _MARKED, claude_surface=None)

    assert submit_calls
    assert submit_calls[-1].startswith("Use the reasoning-posture skill")
    panel_mock.assert_awaited_once()
