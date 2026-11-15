"""Hermetic tests for claude.ai Project name selection before compose submit."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cdp_ask.models import SubmitProjectAskRequest
from cdp_ask.project_select import (
    DEFAULT_CLAUDE_PROJECT_NAME,
    ClaudeProjectSelectError,
    apply_project_select,
    project_select_needed,
)

pytestmark = pytest.mark.offline


def test_submit_model_default_claude_project_name_is_test() -> None:
    req = SubmitProjectAskRequest(prompt_text="hello")
    assert req.claude_project_name == DEFAULT_CLAUDE_PROJECT_NAME


def test_project_select_needed_skips_explicit_uuid() -> None:
    req = SubmitProjectAskRequest(prompt_text="x", project_uuid="uuid-1")
    assert project_select_needed(req) is False


def test_project_select_needed_skips_code_surface() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="x",
        converse=True,
        no_project_uuid=True,
        claude_surface="code",
    )
    assert project_select_needed(req) is False


@pytest.mark.asyncio
async def test_apply_project_select_default_test() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="probe",
        converse=True,
        no_project_uuid=True,
    )
    with patch(
        "cdp_ask.project_select.connect_cdp",
        new=AsyncMock(return_value=(MagicMock(), None, None, MagicMock())),
    ) as connect:
        connect.return_value[0].stop = AsyncMock()
        with patch(
            "cdp_ask.project_select.resolve_project_uuid_by_name",
            new=AsyncMock(return_value="proj-test-uuid"),
        ) as resolve:
            out = await apply_project_select(req, cdp_url="http://127.0.0.1:9226")
    resolve.assert_awaited_once()
    assert resolve.await_args.args[1] == "test"
    assert out.project_uuid == "proj-test-uuid"
    assert out.no_project_uuid is False


@pytest.mark.asyncio
async def test_apply_project_select_override_name() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="probe",
        converse=True,
        no_project_uuid=True,
        claude_project_name="staging",
    )
    with patch(
        "cdp_ask.project_select.connect_cdp",
        new=AsyncMock(return_value=(MagicMock(), None, None, MagicMock())),
    ) as connect:
        connect.return_value[0].stop = AsyncMock()
        with patch(
            "cdp_ask.project_select.resolve_project_uuid_by_name",
            new=AsyncMock(return_value="proj-staging-uuid"),
        ) as resolve:
            out = await apply_project_select(req, cdp_url="http://127.0.0.1:9226")
    resolve.assert_awaited_once()
    assert resolve.await_args.args[1] == "staging"
    assert out.project_uuid == "proj-staging-uuid"
    assert out.claude_project_name == "staging"


@pytest.mark.asyncio
async def test_apply_project_select_not_found_fail_closed() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="probe",
        converse=True,
        no_project_uuid=True,
        claude_project_name="missing",
    )
    with patch(
        "cdp_ask.project_select.connect_cdp",
        new=AsyncMock(return_value=(MagicMock(), None, None, MagicMock())),
    ) as connect:
        connect.return_value[0].stop = AsyncMock()
        with patch(
            "cdp_ask.project_select.resolve_project_uuid_by_name",
            new=AsyncMock(side_effect=ClaudeProjectSelectError("claude project not found: 'missing'")),
        ):
            with pytest.raises(ClaudeProjectSelectError, match="not found"):
                await apply_project_select(req, cdp_url="http://127.0.0.1:9226")


@pytest.mark.asyncio
async def test_run_execution_selects_before_conversation() -> None:
    from cdp_ask.runner import run_execution

    req = SubmitProjectAskRequest(
        prompt_text="probe",
        converse=True,
        no_project_uuid=True,
        claude_project_name="test",
        model="opus-5",
    )
    reg = MagicMock()
    reg.registration_id = "reg-test"
    reg.cdp_url = "http://127.0.0.1:9226"
    reg.target_id = None

    ok_result = MagicMock(
        ok=True,
        body="ok",
        url="https://claude.ai/cowork/cse_x",
        project_uuid="proj-test-uuid",
        project_url="https://claude.ai/cowork/project/proj-test-uuid",
        model={},
        body_len=2,
        delete_after=False,
        error=None,
        archive_uri="cortex://x",
        attested_model=None,
        harvest_provenance=None,
    )

    async def _converse(*_args, **_kwargs):
        return [ok_result]

    converse = AsyncMock(side_effect=_converse)

    async def _overlay(*, run_once, **_kw):
        return await run_once(req.model), {}

    selected = req.model_copy(
        update={"project_uuid": "proj-test-uuid", "no_project_uuid": False}
    )

    with (
        patch("cdp_ask.runner.resolve_prompt", return_value=["probe"]),
        patch("cdp_ask.runner.bind_execution_lane", return_value=reg),
        patch(
            "cdp_ask.runner.apply_project_select",
            new=AsyncMock(return_value=selected),
        ),
        patch(
            "cdp_ask.runner.run_with_overlay_retry", new=AsyncMock(side_effect=_overlay)
        ),
        patch("cdp_ask.runner.run_project_conversation", new=converse),
        patch("cdp_ask.runner.archive_harvest", return_value="cortex://x"),
        patch("cdp_ask.runner._persist_session_address"),
    ):
        result = await run_execution(req, execution_id="exec-1", abort_check=AsyncMock(return_value=False))

    assert result["ok"] is True
    converse.assert_awaited_once()
    assert converse.await_args.kwargs["project_uuid"] == "proj-test-uuid"
