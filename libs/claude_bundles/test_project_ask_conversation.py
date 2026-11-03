"""Offline tests for converse attestation parity (todo:cdp-converse-attestation-parity)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_bundles.cowork_output_download import HarvestBody
from claude_bundles.project_ask import ProjectAskResult
from claude_bundles.project_ask_conversation import (
    project_followup_on_page,
    run_project_conversation,
)

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_followup_stamps_attested_model() -> None:
    page = AsyncMock()
    state = {
        "body": "SKILLS_PROBE_OK",
        "url": "https://claude.ai/chat/abc",
        "model_label": "Model: Fable 5 High",
    }
    harvest = HarvestBody(content="SKILLS_PROBE_OK", provenance="chat")

    with (
        patch(
            "claude_bundles.project_ask_conversation.harvest_assistant",
            new=AsyncMock(return_value={"count": 1}),
        ),
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.project_ask_conversation.wait_assistant_reply",
            new=AsyncMock(return_value=state),
        ),
        patch(
            "claude_bundles.project_ask_conversation.resolve_harvest_body",
            new=AsyncMock(return_value=harvest),
        ),
        patch(
            "claude_bundles.project_ask_conversation._attest_model",
            return_value="Model: Fable 5 High",
        ) as attest,
    ):
        result = await project_followup_on_page(
            page,
            "follow up",
            model="fable-5",
            project_uuid="",
        )

    attest.assert_called_once()
    assert result.ok is True
    assert result.attested_model == "Model: Fable 5 High"


@pytest.mark.asyncio
async def test_compose_success_stamps_attested_model() -> None:
    page = AsyncMock()
    page.url = "https://claude.ai/new"
    state = {
        "body": "compose reply",
        "url": page.url,
        "model_label": "Model: Opus 5",
    }
    harvest = HarvestBody(content="compose reply", provenance="chat")

    with (
        patch(
            "claude_bundles.project_ask_conversation.connect_cdp",
            new=AsyncMock(return_value=(AsyncMock(), None, None, None)),
        ),
        patch(
            "claude_bundles.project_ask_conversation.pick_chat_page",
            new=AsyncMock(return_value=page),
        ),
        patch(
            "claude_bundles.project_ask_conversation._compose_model_selected",
            new=AsyncMock(return_value={"ok": True, "current_model": "Model: Opus 5"}),
        ),
        patch(
            "claude_bundles.project_ask_conversation.harvest_assistant",
            new=AsyncMock(return_value={"count": 0}),
        ),
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.project_ask_conversation.wait_assistant_reply",
            new=AsyncMock(return_value=state),
        ),
        patch(
            "claude_bundles.project_ask_conversation.resolve_harvest_body",
            new=AsyncMock(return_value=harvest),
        ),
        patch(
            "claude_bundles.project_ask_conversation._attest_model",
            return_value="Model: Opus 5",
        ) as attest,
    ):
        results = await run_project_conversation(
            ["first prompt"],
            model="opus-5",
        )

    attest.assert_called_once()
    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].attested_model == "Model: Opus 5"


@pytest.mark.asyncio
async def test_compose_attestation_mismatch_returns_ok_false_list() -> None:
    page = AsyncMock()
    page.url = "https://claude.ai/new"
    state = {"body": "real reply", "url": page.url}

    with (
        patch(
            "claude_bundles.project_ask_conversation.connect_cdp",
            new=AsyncMock(return_value=(AsyncMock(), None, None, None)),
        ),
        patch(
            "claude_bundles.project_ask_conversation.pick_chat_page",
            new=AsyncMock(return_value=page),
        ),
        patch(
            "claude_bundles.project_ask_conversation._compose_model_selected",
            new=AsyncMock(return_value={"ok": True, "current_model": "Model: Opus 5"}),
        ),
        patch(
            "claude_bundles.project_ask_conversation.harvest_assistant",
            new=AsyncMock(return_value={"count": 0}),
        ),
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.project_ask_conversation.wait_assistant_reply",
            new=AsyncMock(return_value=state),
        ),
        patch(
            "claude_bundles.project_ask_conversation._attest_model",
            side_effect=RuntimeError("model attestation mismatch"),
        ),
    ):
        results = await run_project_conversation(
            ["first prompt"],
            model="fable-5",
        )

    assert len(results) == 1
    assert results[0].ok is False
    assert results[0].attested_model is None
    assert "attestation mismatch" in (results[0].error or "")


@pytest.mark.asyncio
async def test_converse_path_threads_correlation_ids_to_send_prompt() -> None:
    """AC-CORR converse path — red if ids only thread on non-converse escape.

    Pre-``582f3ff9`` plumbing bound ids through ``project_ask_on_page`` →
    ``send_prompt`` (MCP ``converse=False``). Production inherits
    ``converse=True`` → ``run_project_conversation``, which had no id params and
    called ``send_prompt(page, prompt)`` bare. Under that tree this test fails
    with ``TypeError: unexpected keyword argument`` (or, if kwargs were silently
    dropped at the conversation boundary, with missing send_prompt kwargs).
    Green here certifies the production branch, not only the escape branch.
    """
    page = AsyncMock()
    page.url = "https://claude.ai/new"
    state = {
        "body": "reply body long enough for harvest",
        "url": page.url,
        "model_label": "Model: Opus 5",
    }
    harvest = HarvestBody(
        content="reply body long enough for harvest", provenance="chat"
    )
    send_prompt = AsyncMock()

    with (
        patch(
            "claude_bundles.project_ask_conversation.connect_cdp",
            new=AsyncMock(return_value=(AsyncMock(), None, None, None)),
        ),
        patch(
            "claude_bundles.project_ask_conversation.pick_chat_page",
            new=AsyncMock(return_value=page),
        ),
        patch(
            "claude_bundles.project_ask_conversation._compose_model_selected",
            new=AsyncMock(return_value={"ok": True, "current_model": "Model: Opus 5"}),
        ),
        patch(
            "claude_bundles.project_ask_conversation.harvest_assistant",
            new=AsyncMock(return_value={"count": 0}),
        ),
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new=send_prompt,
        ),
        patch(
            "claude_bundles.project_ask_conversation.wait_assistant_reply",
            new=AsyncMock(return_value=state),
        ),
        patch(
            "claude_bundles.project_ask_conversation._attest_model",
            return_value="Model: Opus 5",
        ),
        patch(
            "claude_bundles.project_ask_conversation.resolve_harvest_body",
            new=AsyncMock(return_value=harvest),
        ),
    ):
        results = await run_project_conversation(
            ["first prompt"],
            model="opus-5",
            stargate_execution_id="sg-exec-corr-1",
            satellite_execution_id="sat-exec-corr-1",
        )

    assert len(results) == 1
    assert results[0].ok is True
    send_prompt.assert_called_once()
    kwargs = send_prompt.call_args.kwargs
    assert kwargs.get("stargate_execution_id") == "sg-exec-corr-1"
    assert kwargs.get("satellite_execution_id") == "sat-exec-corr-1"


@pytest.mark.asyncio
async def test_converse_emits_page_url_after_send_prompt() -> None:
    """a:30678 — bind cse_ URL even when wait fails after send."""
    page = AsyncMock()
    page.url = "https://claude.ai/new"
    captured: list[str] = []

    async def _send(*_args, **_kwargs):
        page.url = "https://claude.ai/cowork/cse_after_send"

    async def _harvest(state):
        captured.append(str(state.get("url") or ""))

    with (
        patch(
            "claude_bundles.project_ask_conversation.connect_cdp",
            new=AsyncMock(return_value=(AsyncMock(), None, None, None)),
        ),
        patch(
            "claude_bundles.project_ask_conversation.pick_chat_page",
            new=AsyncMock(return_value=page),
        ),
        patch(
            "claude_bundles.project_ask_conversation._compose_model_selected",
            new=AsyncMock(return_value={"ok": True, "current_model": "Model: Fable 5"}),
        ),
        patch(
            "claude_bundles.project_ask_conversation.harvest_assistant",
            new=AsyncMock(return_value={"count": 0}),
        ),
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new=AsyncMock(side_effect=_send),
        ),
        patch(
            "claude_bundles.project_ask_conversation.wait_assistant_reply",
            new=AsyncMock(side_effect=RuntimeError("wait failed")),
        ),
    ):
        with pytest.raises(RuntimeError, match="wait failed"):
            await run_project_conversation(
                ["first prompt"],
                model="fable-5",
                on_harvest=_harvest,
            )

    assert "https://claude.ai/cowork/cse_after_send" in captured


@pytest.mark.asyncio
async def test_converse_path_threads_ids_via_project_ask_on_page() -> None:
    """Converse + project_uuid must pass Stargate/satellite ids into on_page."""
    from claude_bundles.project_ask import ProjectAskResult

    page = AsyncMock()
    page.url = "https://claude.ai/project/abc"
    on_page = AsyncMock(
        return_value=ProjectAskResult(
            ok=True,
            body="ok",
            url=page.url,
            project_uuid="proj-uuid",
            project_url="https://claude.ai/project/proj-uuid",
            model={"ok": True},
            body_len=2,
            delete_after=None,
        )
    )

    with (
        patch(
            "claude_bundles.project_ask_conversation.connect_cdp",
            new=AsyncMock(return_value=(AsyncMock(), None, None, None)),
        ),
        patch(
            "claude_bundles.project_ask_conversation.pick_chat_page",
            new=AsyncMock(return_value=page),
        ),
        patch(
            "claude_bundles.project_ask_conversation.project_ask_on_page",
            new=on_page,
        ),
    ):
        results = await run_project_conversation(
            ["first prompt"],
            project_uuid="proj-uuid",
            model="opus-5",
            delete_after=False,
            stargate_execution_id="sg-exec-corr-2",
            satellite_execution_id="sat-exec-corr-2",
        )

    assert results[0].ok is True
    kwargs = on_page.call_args.kwargs
    assert kwargs.get("stargate_execution_id") == "sg-exec-corr-2"
    assert kwargs.get("execution_id") == "sat-exec-corr-2"


_MARKED_REASONING = "<!--cdp-required-skills:reasoning-posture-->\n# Work body\n"


@pytest.mark.asyncio
async def test_converse_project_uuid_send_prompt_receives_claude_surface() -> None:
    """project_uuid + claude_surface=code threads surface into send_prompt."""
    page = AsyncMock()
    page.url = "https://claude.ai/project/proj-uuid"
    send_prompt = AsyncMock()
    state = {
        "body": "reply " + ("y" * 120),
        "url": page.url,
        "model_label": "Model: Opus 5",
    }
    harvest = HarvestBody(content=state["body"], provenance="chat")

    with (
        patch(
            "claude_bundles.project_ask_conversation.connect_cdp",
            new=AsyncMock(return_value=(AsyncMock(), None, None, None)),
        ),
        patch(
            "claude_bundles.project_ask_conversation.pick_chat_page",
            new=AsyncMock(return_value=page),
        ),
        patch(
            "claude_bundles.project_ask._compose_model_selected",
            new=AsyncMock(return_value={"ok": True, "current_model": "Opus 5"}),
        ),
        patch(
            "claude_bundles.project_ask.harvest_assistant",
            new=AsyncMock(return_value={"count": 0}),
        ),
        patch(
            "claude_bundles.project_ask.send_prompt",
            new=send_prompt,
        ),
        patch(
            "claude_bundles.project_ask.wait_assistant_reply",
            new=AsyncMock(return_value=state),
        ),
        patch(
            "claude_bundles.project_ask.resolve_harvest_body",
            new=AsyncMock(return_value=harvest),
        ),
        patch(
            "claude_bundles.project_ask._attest_model",
            return_value="Model: Opus 5",
        ),
    ):
        results = await run_project_conversation(
            [_MARKED_REASONING],
            project_uuid="proj-uuid",
            model="opus-5",
            claude_surface="code",
            delete_after=False,
        )

    assert len(results) == 1
    assert results[0].ok is True
    assert send_prompt.await_count == 1
    assert send_prompt.call_args.kwargs.get("claude_surface") == "code"


@pytest.mark.asyncio
async def test_project_uuid_code_surface_inline_attest_via_on_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """project_uuid path: /code + marked prompt uses inline seals and post-submit attest."""
    from contextlib import ExitStack

    from claude_bundles.chat_context_skills import ChatContextSkillsError
    from claude_bundles.project_ask import project_ask_on_page

    page = AsyncMock()
    composer = AsyncMock()
    submit_calls: list[str] = []
    attest_payloads: list[dict] = []

    async def _capture_submit(*_args, **kwargs):
        submit_calls.append(kwargs.get("draft_text") or "")

    def _capture_attest(**payload):
        attest_payloads.append(dict(payload))

    patches = (
        patch(
            "claude_bundles.project_ask._compose_model_selected",
            new=AsyncMock(return_value={"ok": True, "current_model": "Opus 5"}),
        ),
        patch(
            "claude_bundles.project_ask.harvest_assistant",
            new=AsyncMock(return_value={"count": 0}),
        ),
        patch(
            "claude_bundles.project_ask.wait_assistant_reply",
            new=AsyncMock(
                return_value={
                    "body": "done " + ("z" * 120),
                    "url": page.url,
                    "model_label": "Opus 5",
                }
            ),
        ),
        patch(
            "claude_bundles.project_ask.resolve_harvest_body",
            new=AsyncMock(
                return_value=HarvestBody(content="done " + ("z" * 120), provenance="chat")
            ),
        ),
        patch(
            "claude_bundles.project_ask._attest_model",
            return_value="Model: Opus 5",
        ),
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
            side_effect=ChatContextSkillsError("Context → Skills not on the rail"),
        ),
        patch(
            "claude_bundles.skill_context_receipt.record_post_submit_skills_receipt",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.cowork_skill_delivery.emit_skill_delivery_attested",
            side_effect=_capture_attest,
        ),
        patch.object(page, "wait_for_timeout", new=AsyncMock()),
    )

    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        stack.enter_context(
            patch(
                "claude_bundles.cowork_skill_delivery.await_committed_skill_seals",
                new=AsyncMock(return_value=["reasoning-posture"]),
            )
        )
        result = await project_ask_on_page(
            page,
            _MARKED_REASONING,
            project_uuid="proj-uuid",
            claude_surface="code",
            delete_after=False,
        )

    assert result.ok is True
    assert submit_calls
    draft = submit_calls[-1]
    assert '<skill slug="reasoning-posture"' in draft
    assert "Use the reasoning-posture skill" not in draft
    assert attest_payloads
    last = attest_payloads[-1]
    assert last["ok"] is True
    assert last["claude_surface"] == "code"
    assert last["induction"] == []


@pytest.mark.asyncio
async def test_project_uuid_code_surface_missing_seal_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """project_uuid /code path fails closed when inline seal is not observed."""
    from contextlib import ExitStack

    from claude_bundles.chat_context_skills import ChatContextSkillsError
    from claude_bundles.cowork_skill_delivery import SkillReceiptUnverifiedError
    from claude_bundles.project_ask import project_ask_on_page, send_prompt as real_send_prompt

    page = AsyncMock()
    composer = AsyncMock()
    receipt_errors: list[SkillReceiptUnverifiedError] = []

    async def _send_raises_on_bad_seal(*args, **kwargs):
        try:
            return await real_send_prompt(*args, **kwargs)
        except SkillReceiptUnverifiedError as exc:
            receipt_errors.append(exc)
            raise

    patches = (
        patch(
            "claude_bundles.project_ask._compose_model_selected",
            new=AsyncMock(return_value={"ok": True, "current_model": "Opus 5"}),
        ),
        patch(
            "claude_bundles.project_ask.harvest_assistant",
            new=AsyncMock(return_value={"count": 0, "n": 0, "body_len": 0}),
        ),
        patch(
            "claude_bundles.project_ask.send_prompt",
            side_effect=_send_raises_on_bad_seal,
        ),
        patch(
            "claude_bundles.project_ask.wait_assistant_reply",
            new=AsyncMock(
                return_value={
                    "body": "partial " + ("p" * 80),
                    "url": "https://claude.ai/project/proj-uuid",
                    "n": 1,
                    "model_label": "Model: Opus 5",
                }
            ),
        ),
        patch(
            "claude_bundles.project_ask._attest_model",
            return_value="Model: Opus 5",
        ),
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
        stack.enter_context(
            patch(
                "claude_bundles.cowork_skill_delivery.await_committed_skill_seals",
                new=AsyncMock(return_value=[]),
            )
        )
        result = await project_ask_on_page(
            page,
            _MARKED_REASONING,
            project_uuid="proj-uuid",
            claude_surface="code",
            delete_after=False,
        )

    assert receipt_errors
    assert "undelivered" in str(receipt_errors[0])
    assert receipt_errors[0].submitted is True
    assert result.ok is False
    assert "skill_receipt_unverified" in (result.error or "")


@pytest.mark.asyncio
async def test_followup_harvest_incomplete_preserves_nonzero_body() -> None:
    """a:37226 — timeout with last>0 must not return FAILED body_len=0."""
    from claude_bundles.chat_reply_wait import HarvestIncompleteError

    page = AsyncMock()
    page.url = "https://claude.ai/cowork/cse_01DySavjf3QUK1oDU9NC9McG"
    partial = "Review opening with RETURN_TO_DESIGN… " + ("x" * 200)

    with (
        patch(
            "claude_bundles.project_ask_conversation.harvest_assistant",
            new=AsyncMock(return_value={"count": 1}),
        ),
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.project_ask_conversation.wait_assistant_reply",
            new=AsyncMock(
                side_effect=HarvestIncompleteError(
                    "timed out incomplete (base_len=0, last=6969, n=6) — ¬delete",
                    body=partial,
                )
            ),
        ),
    ):
        result = await project_followup_on_page(
            page,
            "follow up",
            model="opus-5",
            project_uuid="",
            purpose="review",
        )

    assert result.ok is False
    assert "timed out incomplete" in (result.error or "")
    assert result.body == partial
    assert result.body_len == len(partial)
    assert result.body_len > 0


@pytest.mark.asyncio
async def test_compose_harvest_incomplete_preserves_nonzero_body() -> None:
    """a:37226 review A2 — compose-first path must keep last scrape."""
    from claude_bundles.chat_reply_wait import HarvestIncompleteError

    page = AsyncMock()
    page.url = "https://claude.ai/new"
    partial = "Compose review mid-body without sealed verdict. " + ("y" * 120)

    with (
        patch(
            "claude_bundles.project_ask_conversation.connect_cdp",
            new=AsyncMock(return_value=(AsyncMock(), None, None, None)),
        ),
        patch(
            "claude_bundles.project_ask_conversation.pick_chat_page",
            new=AsyncMock(return_value=page),
        ),
        patch(
            "claude_bundles.project_ask_conversation._compose_model_selected",
            new=AsyncMock(return_value={"ok": True, "current_model": "Model: Opus 5"}),
        ),
        patch(
            "claude_bundles.project_ask_conversation.harvest_assistant",
            new=AsyncMock(return_value={"count": 0}),
        ),
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.project_ask_conversation.wait_assistant_reply",
            new=AsyncMock(
                side_effect=HarvestIncompleteError(
                    "timed out incomplete (base_len=0, last=6969, n=6) — ¬delete",
                    body=partial,
                )
            ),
        ),
    ):
        results = await run_project_conversation(
            ["first prompt"],
            model="opus-5",
            purpose="review",
            delete_after=False,
        )

    assert len(results) == 1
    assert results[0].ok is False
    assert results[0].body == partial
    assert results[0].body_len == len(partial)
    assert results[0].body_len > 0
    assert "timed out incomplete" in (results[0].error or "")


@pytest.mark.asyncio
async def test_compose_skill_receipt_unverified_harvests_body() -> None:
    """Panel receipt failure on converse first turn → harvest, not mark_terminal."""
    from cdp_ask.unverifiable import converse_stall_stage

    from claude_bundles.cowork_skill_delivery import SkillReceiptUnverifiedError

    page = AsyncMock()
    page.url = "https://claude.ai/cowork/cse_test"
    partial = "Work reply prose after unverified skill receipt. " + ("x" * 80)
    harvest_result = AsyncMock(
        return_value=ProjectAskResult(
            ok=False,
            body=partial,
            url=page.url,
            project_uuid="",
            project_url="https://claude.ai/new",
            model={"ok": True},
            body_len=len(partial),
            delete_after=None,
            error="skill_receipt_unverified: panel timeout",
        )
    )

    with (
        patch(
            "claude_bundles.project_ask_conversation.connect_cdp",
            new=AsyncMock(return_value=(AsyncMock(), None, None, None)),
        ),
        patch(
            "claude_bundles.project_ask_conversation.pick_chat_page",
            new=AsyncMock(return_value=page),
        ),
        patch(
            "claude_bundles.project_ask_conversation._compose_model_selected",
            new=AsyncMock(return_value={"ok": True, "current_model": "Model: Opus 5"}),
        ),
        patch(
            "claude_bundles.project_ask_conversation.harvest_assistant",
            new=AsyncMock(return_value={"count": 0, "n": 0, "body_len": 0}),
        ),
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new=AsyncMock(
                side_effect=SkillReceiptUnverifiedError("panel timeout", submitted=True)
            ),
        ),
        patch(
            "claude_bundles.project_ask_conversation._harvest_after_skill_receipt_unverified",
            new=harvest_result,
        ) as harvest_after,
        patch(
            "claude_bundles.project_ask_conversation.wait_assistant_reply",
            new=AsyncMock(),
        ) as wait_reply,
    ):
        results = await run_project_conversation(
            ["<!--cdp-required-skills:reasoning-posture-->\nDo work"],
            model="opus-5",
            delete_after=False,
        )

    harvest_after.assert_awaited_once()
    wait_reply.assert_not_awaited()
    assert len(results) == 1
    assert results[0].ok is False
    assert results[0].body == partial
    assert results[0].body_len > 0
    assert (
        converse_stall_stage(results[0].error, conv_ok=False) == "observer_unverified"
    )


@pytest.mark.asyncio
async def test_code_surface_two_turn_conversation_passes_every_approval_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R21 /code submit: both turns use code approval gate + live_discover submit."""
    page = AsyncMock()
    page.url = "https://claude.ai/code"
    page.title = AsyncMock(return_value="Claude Code")
    page.evaluate = AsyncMock(return_value={"aria": "", "text": "Auto", "via": "text"})

    seen_surfaces: list[str | None] = []
    submit_mock = AsyncMock()
    model_info: dict = {
        "ok": True,
        "step": "code_surface_page_model",
        "current_model": "Model: Opus 5.5 Medium",
        "model_ran": "Model: Opus 5.5 Medium",
    }

    async def send_shim(
        pg,
        text: str,
        *,
        stargate_execution_id: str = "",
        satellite_execution_id: str = "",
        await_induction_reply: bool = False,
        on_harvest=None,
        claude_surface: str | None = None,
        compose_receipt: dict | None = None,
    ) -> None:
        seen_surfaces.append(claude_surface)
        from claude_bundles.project_ask import _submit_composer_draft

        await _submit_composer_draft(
            pg,
            composer=AsyncMock(),
            draft_text=text,
            claude_surface=claude_surface,
            compose_receipt=compose_receipt,
        )

    monkeypatch.setattr(
        "claude_bundles.chat_cowork_mode.ensure_approval_auto",
        AsyncMock(side_effect=AssertionError("ensure_approval_auto must not run")),
    )
    monkeypatch.setattr(
        "claude_bundles.chat_cowork_mode.set_approval_mode",
        AsyncMock(side_effect=AssertionError("set_approval_mode must not run")),
    )
    monkeypatch.setattr(
        "claude_bundles.composer_submit.submit_composer_content",
        submit_mock,
    )

    state = {
        "body": "code surface reply prose. " + ("z" * 120),
        "url": page.url,
        "model_label": "Model: Opus 5.5 Medium",
    }
    harvest = HarvestBody(content=state["body"], provenance="chat")

    with (
        patch(
            "claude_bundles.project_ask_conversation.connect_cdp",
            new=AsyncMock(return_value=(AsyncMock(), None, None, None)),
        ),
        patch(
            "claude_bundles.project_ask_conversation.pick_chat_page",
            new=AsyncMock(return_value=page),
        ),
        patch(
            "claude_bundles.project_ask_conversation._compose_model_selected",
            new=AsyncMock(return_value=model_info),
        ),
        patch(
            "claude_bundles.project_ask_conversation.harvest_assistant",
            new=AsyncMock(return_value={"count": 0}),
        ),
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new=AsyncMock(side_effect=send_shim),
        ),
        patch(
            "claude_bundles.project_ask_conversation.wait_assistant_reply",
            new=AsyncMock(return_value=state),
        ),
        patch(
            "claude_bundles.project_ask_conversation.resolve_harvest_body",
            new=AsyncMock(return_value=harvest),
        ),
        patch(
            "claude_bundles.project_ask_conversation._attest_model",
            return_value="Model: Opus 5.5 Medium",
        ),
    ):
        results = await run_project_conversation(
            ["first", "second"],
            model="opus-5.5-medium",
            compose_url="https://claude.ai/code",
            claude_surface="code",
            delete_after=False,
        )

    assert len(results) == 2
    assert all(r.ok for r in results)
    assert seen_surfaces == ["code", "code"]
    assert submit_mock.await_count == 2
    assert model_info["approval_mode"] == "auto"


@pytest.mark.asyncio
async def test_code_surface_model_mismatch_fails_before_send_prompt() -> None:
    """R23: /code mismatch raises before send_prompt (no harvest burn)."""
    page = AsyncMock()
    page.url = "https://claude.ai/code"
    model_info = {
        "ok": True,
        "step": "code_surface_page_model",
        "model_ran": "Model: Opus 5.5 Medium",
        "current_model": "Model: Opus 5.5 Medium",
    }
    send_prompt = AsyncMock()
    wait_reply = AsyncMock()

    with (
        patch(
            "claude_bundles.project_ask_conversation.connect_cdp",
            new=AsyncMock(return_value=(AsyncMock(), None, None, None)),
        ),
        patch(
            "claude_bundles.project_ask_conversation.pick_chat_page",
            new=AsyncMock(return_value=page),
        ),
        patch(
            "claude_bundles.project_ask_conversation._compose_model_selected",
            new=AsyncMock(return_value=model_info),
        ),
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new=send_prompt,
        ),
        patch(
            "claude_bundles.project_ask_conversation.wait_assistant_reply",
            new=wait_reply,
        ),
    ):
        results = await run_project_conversation(
            ["diagnose only"],
            model="sonnet-5-5",
            compose_url="https://claude.ai/code",
            claude_surface="code",
            delete_after=False,
        )

    send_prompt.assert_not_awaited()
    wait_reply.assert_not_awaited()
    assert len(results) == 1
    assert results[0].ok is False
    assert results[0].body_len == 0
    assert "model attestation mismatch" in (results[0].error or "")
    assert "sonnet-5.5" in (results[0].error or "")


def test_assert_code_surface_skips_unknown_compose_label() -> None:
    """Compose ``unknown`` sentinel is not treated as a model mismatch (CDP REVISE)."""
    from claude_bundles.project_ask import _assert_code_surface_model_before_send

    _assert_code_surface_model_before_send(
        "opus-5.5-medium",
        {"model_ran": "unknown", "current_model": "unknown"},
    )


@pytest.mark.asyncio
async def test_code_surface_model_match_calls_send_prompt_once() -> None:
    """R23: matched /code label proceeds to send_prompt."""
    page = AsyncMock()
    page.url = "https://claude.ai/code"
    model_info = {
        "ok": True,
        "step": "code_surface_page_model",
        "model_ran": "Model: Opus 5.5 Medium",
        "current_model": "Model: Opus 5.5 Medium",
    }
    send_prompt = AsyncMock()
    state = {
        "body": "ok " + ("x" * 120),
        "url": page.url,
        "model_label": "Model: Opus 5.5 Medium",
    }
    harvest = HarvestBody(content=state["body"], provenance="chat")

    with (
        patch(
            "claude_bundles.project_ask_conversation.connect_cdp",
            new=AsyncMock(return_value=(AsyncMock(), None, None, None)),
        ),
        patch(
            "claude_bundles.project_ask_conversation.pick_chat_page",
            new=AsyncMock(return_value=page),
        ),
        patch(
            "claude_bundles.project_ask_conversation._compose_model_selected",
            new=AsyncMock(return_value=model_info),
        ),
        patch(
            "claude_bundles.project_ask_conversation.harvest_assistant",
            new=AsyncMock(return_value={"count": 0}),
        ),
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new=send_prompt,
        ),
        patch(
            "claude_bundles.project_ask_conversation.wait_assistant_reply",
            new=AsyncMock(return_value=state),
        ),
        patch(
            "claude_bundles.project_ask_conversation.resolve_harvest_body",
            new=AsyncMock(return_value=harvest),
        ),
    ):
        results = await run_project_conversation(
            ["work"],
            model="opus-5.5-medium",
            compose_url="https://claude.ai/code",
            claude_surface="code",
            delete_after=False,
        )

    send_prompt.assert_awaited_once()
    assert len(results) == 1
    assert results[0].ok is True


@pytest.mark.asyncio
async def test_compose_new_path_skips_code_surface_pre_send_assert() -> None:
    """Chat/cowork first compose does not run the /code pre-send model gate."""
    page = AsyncMock()
    page.url = "https://claude.ai/new"
    model_info = {"ok": True, "step": "selected", "current_model": "Opus 4.6"}
    pre_assert = MagicMock(side_effect=AssertionError("must not run on /new"))

    with (
        patch(
            "claude_bundles.project_ask_conversation.connect_cdp",
            new=AsyncMock(return_value=(AsyncMock(), None, None, None)),
        ),
        patch(
            "claude_bundles.project_ask_conversation.pick_chat_page",
            new=AsyncMock(return_value=page),
        ),
        patch(
            "claude_bundles.project_ask_conversation._compose_model_selected",
            new=AsyncMock(return_value=model_info),
        ),
        patch(
            "claude_bundles.project_ask_conversation._assert_code_surface_model_before_send",
            new=pre_assert,
        ),
        patch(
            "claude_bundles.project_ask_conversation.harvest_assistant",
            new=AsyncMock(return_value={"count": 0}),
        ),
        patch(
            "claude_bundles.project_ask_conversation.send_prompt",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.project_ask_conversation.wait_assistant_reply",
            new=AsyncMock(
                return_value={
                    "body": "reply " + ("y" * 120),
                    "url": page.url,
                    "model_label": "Opus 4.6",
                }
            ),
        ),
        patch(
            "claude_bundles.project_ask_conversation.resolve_harvest_body",
            new=AsyncMock(return_value=HarvestBody(content="reply", provenance="chat")),
        ),
        patch(
            "claude_bundles.project_ask_conversation._attest_model",
            return_value="Opus 4.6",
        ),
    ):
        results = await run_project_conversation(
            ["hi"],
            compose_url="https://claude.ai/new",
            delete_after=False,
        )

    pre_assert.assert_not_called()
    assert results[0].ok is True


# Dispatch gates may target this module; implementation lives in test_compose_attest.
from claude_bundles.test_compose_attest import (  # noqa: E402
    test_code_surface_never_awaits_ensure_approval_auto,  # noqa: F401
)
