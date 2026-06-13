"""Unit tests for DensifyCandidateReady handler."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Response

from systems.frontier_consult.densify_candidate_ready import (
    DensifyCandidateReadyBody,
    handle_densify_candidate_ready,
    spawn_densify_reviewer_child,
)
from systems.frontier_consult.densify_triage import (
    COMPOSER_DRAFT_SENTINEL,
    REASONING_TRACE_SENTINEL,
    SEED_ONLY_SENTINEL,
)
from systems.frontier_consult.events import FrontierDensifyReviewAdmitted


@pytest.mark.asyncio
async def test_blank_adequacy_holds_without_spawn() -> None:
    body = DensifyCandidateReadyBody(
        draft_adequacy="blank",
        staged_draft_uri="workspaces:spec.md",
        reasoning_trace_uri="workspaces:spec.md#reasoning_trace",
        parent_dispatch_thread_id="thread:1805",
        density_triage="judgment_required",
    )
    events: list[FrontierDensifyReviewAdmitted] = []

    result = await handle_densify_candidate_ready(
        request_id="req-blank",
        body=body,
        response=Response(),
        event_publisher=events.append,
    )
    assert result["status"] == "hold"
    assert result["review_spawned"] is False
    assert len(events) == 1
    assert events[0].payload["hold_reason"] == "blank_adequacy"


@pytest.mark.asyncio
async def test_partial_default_on_spawns_reviewer_with_sentinels() -> None:
    body = DensifyCandidateReadyBody(
        draft_adequacy="partial",
        staged_draft_uri="workspaces:spec.md",
        reasoning_trace_uri="workspaces:spec.md#reasoning_trace",
        parent_dispatch_thread_id="thread:1805",
        parent_execution_id="exec-composer-1",
        density_triage="dispatch_surface",
        staged_draft_body=f"{COMPOSER_DRAFT_SENTINEL}\n# staged spec",
        reasoning_trace_body=f"{REASONING_TRACE_SENTINEL}\n# trace",
    )
    events: list[FrontierDensifyReviewAdmitted] = []

    with patch(
        "systems.frontier_consult.densify_candidate_ready.spawn_densify_reviewer_child",
        new_callable=AsyncMock,
        return_value={"execution_id": "exec-review-1"},
    ) as spawn:
        result = await handle_densify_candidate_ready(
            request_id="req-partial",
            body=body,
            response=Response(),
            event_publisher=events.append,
        )

    spawn.assert_awaited_once()
    prompt = spawn.await_args.kwargs["reviewer_prompt"]
    assert COMPOSER_DRAFT_SENTINEL in prompt
    assert REASONING_TRACE_SENTINEL in prompt
    assert SEED_ONLY_SENTINEL not in prompt
    assert result["review_spawned"] is True
    assert result["auto_review_spawned"] is True
    assert len(events) == 1
    assert events[0].payload["review_spawned"] is True


@pytest.mark.asyncio
async def test_valid_opt_out_no_spawn_preserves_advisory() -> None:
    body = DensifyCandidateReadyBody(
        draft_adequacy="adequate",
        staged_draft_uri="workspaces:spec.md",
        reasoning_trace_uri="workspaces:spec.md#reasoning_trace",
        parent_dispatch_thread_id="thread:1805",
        density_triage="judgment_required",
        review_opt_out_reason_code="routine_single_subsystem",
    )
    events: list[FrontierDensifyReviewAdmitted] = []

    with patch(
        "systems.frontier_consult.densify_candidate_ready.spawn_densify_reviewer_child",
        new_callable=AsyncMock,
    ) as spawn:
        result = await handle_densify_candidate_ready(
            request_id="req-optout",
            body=body,
            response=Response(),
            event_publisher=events.append,
        )

    spawn.assert_not_awaited()
    assert result["recommended_review"] == "cross-family-reconcile:default-on"
    assert result["review_opted_out"] is True
    assert result["auto_review_spawned"] is False
    assert events[0].payload["opt_out"] is True


@pytest.mark.asyncio
async def test_spawn_child_carries_auto_review_child() -> None:
    with (
        patch(
            "systems.frontier_consult.route.team_dispatch",
            new_callable=AsyncMock,
            return_value={"execution_id": "child-exec"},
        ) as dispatch,
        patch(
            "systems.frontier_consult.dispatch_thread_context.read_latest_dispatch_thread_body",
            new_callable=AsyncMock,
            return_value="prompt",
        ),
    ):
        result = await spawn_densify_reviewer_child(
            request_id="req-child",
            parent_dispatch_thread_id="thread:1805",
            reviewer_prompt=f"{COMPOSER_DRAFT_SENTINEL}\n{REASONING_TRACE_SENTINEL}",
            response=Response(),
        )

    dispatch.assert_awaited_once()
    child_body = dispatch.await_args.args[0]
    assert child_body.op == "to_thread"
    assert child_body.auto_review_child is True
    assert child_body.thread == "thread:1805"
    assert result["execution_id"] == "child-exec"
