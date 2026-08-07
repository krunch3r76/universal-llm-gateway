"""AC-CORR converse-path threading — production branch must carry correlation ids."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from claude_bundles.cowork_output_download import HarvestBody
from claude_bundles.project_ask import ProjectAskResult
from claude_bundles.project_ask_conversation import run_project_conversation

pytestmark = pytest.mark.offline


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
async def test_converse_path_threads_ids_via_project_ask_on_page() -> None:
    """Converse + project_uuid must pass Stargate/satellite ids into on_page."""
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
