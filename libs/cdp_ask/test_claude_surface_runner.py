"""Offline tests for claude_surface compose routing on CDP submit."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cdp_ask.models import SubmitProjectAskRequest
from cdp_ask.runner import _compose_args_for_surface, run_execution

pytestmark = pytest.mark.offline


def test_compose_args_code_surface() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="hi",
        converse=True,
        no_project_uuid=True,
        claude_surface="code",
        ensure_cowork_auto=True,
    )
    url, ensure = _compose_args_for_surface(req)
    assert url == "https://claude.ai/code"
    assert ensure is False


def test_compose_args_cowork_default() -> None:
    req = SubmitProjectAskRequest(prompt_text="hi", ensure_cowork_auto=True)
    url, ensure = _compose_args_for_surface(req)
    assert url is None
    assert ensure is True


@pytest.mark.asyncio
async def test_run_execution_passes_code_compose_to_conversation() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="probe",
        converse=True,
        no_project_uuid=True,
        claude_surface="code",
        model="opus-5",
    )
    reg = MagicMock()
    reg.registration_id = "reg-test"
    reg.cdp_url = "http://127.0.0.1:9226"
    reg.target_id = None

    ok_result = MagicMock(
        ok=True,
        body="ok",
        url="https://claude.ai/code/session_testid",
        project_uuid="",
        project_url="https://claude.ai/code",
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

    with (
        patch("cdp_ask.runner.resolve_prompt", return_value=["probe"]),
        patch("cdp_ask.runner.bind_execution_lane", return_value=reg),
        patch(
            "cdp_ask.runner.run_with_overlay_retry", new=AsyncMock(side_effect=_overlay)
        ),
        patch("cdp_ask.runner.run_project_conversation", new=converse),
        patch("cdp_ask.runner.archive_harvest", return_value="cortex://x"),
    ):
        result = await run_execution(
            req,
            execution_id="exec-test",
            abort_check=AsyncMock(return_value=False),
        )

    converse.assert_awaited_once()
    kwargs = converse.await_args.kwargs
    assert kwargs["compose_url"] == "https://claude.ai/code"
    assert kwargs["ensure_cowork_auto"] is False
    assert kwargs["claude_surface"] == "code"
    assert result["url"] == "https://claude.ai/code/session_testid"
