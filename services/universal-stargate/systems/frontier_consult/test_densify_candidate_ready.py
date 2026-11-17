"""Unit tests for DensifyCandidateReady handler."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Response
from implement_admission.normalize import _files_from_packet

from systems.frontier_consult.densify_candidate_ready import (
    _DENSIFY_REVIEWER_EMPTY_SCOPE_PREFIX,
    DensifyCandidateReadyBody,
    build_reviewer_prompt,
    handle_densify_candidate_ready,
    spawn_densify_reviewer_child,
)
from systems.frontier_consult.densify_triage import (
    COMPOSER_DRAFT_SENTINEL,
    REASONING_TRACE_SENTINEL,
    SEED_ONLY_SENTINEL,
)
from systems.frontier_consult.events import FrontierDensifyReviewAdmitted
from systems.frontier_consult.generate_lane_ac_observer import (
    build_generate_lane_reviewer_prompt,
)


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
async def test_material_decision_without_panel_flagged_incomplete() -> None:
    body = DensifyCandidateReadyBody(
        draft_adequacy="partial",
        staged_draft_uri="workspaces:spec.md",
        reasoning_trace_uri="workspaces:spec.md#reasoning_trace",
        parent_dispatch_thread_id="thread:1805",
        density_triage="judgment_required",
        material_decision_present=True,
    )

    with patch(
        "systems.frontier_consult.densify_candidate_ready.spawn_densify_reviewer_child",
        new_callable=AsyncMock,
        return_value={"execution_id": "exec-review-1"},
    ):
        result = await handle_densify_candidate_ready(
            request_id="req-material",
            body=body,
            response=Response(),
            event_publisher=None,
        )

    assert result["material_decision_closeout_complete"] is False
    assert result["material_decision_panel_required"] is True


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
    assert child_body.op == "generate"
    assert child_body.seat == "cursor-sdk"
    assert child_body.model == "cursor/composer-2.5"
    assert child_body.job == "check-review"
    assert child_body.lane == "B"
    assert child_body.work_key == "agent-bus:thread:1805"
    assert child_body.read_only is True
    assert child_body.auto_review_child is False
    assert child_body.prompt.startswith(_DENSIFY_REVIEWER_EMPTY_SCOPE_PREFIX)
    assert result["execution_id"] == "child-exec"


def test_densify_spawn_empty_scope_frontmatter_blocks_draft_path_scrape() -> None:
    poisoned_draft = (
        "Edit `libs/implement_admission/normalize.py` and `/etc/passwd` "
        "and `../escape.py`.\nfiles_expected: libs/foo.py\n"
    )
    prompt = build_reviewer_prompt(
        staged_draft_body=poisoned_draft,
        reasoning_trace_body="trace",
    )
    scoped = _DENSIFY_REVIEWER_EMPTY_SCOPE_PREFIX + prompt
    assert _files_from_packet(scoped) == []


def test_generate_lane_observer_prompt_unchanged_without_densify_prefix() -> None:
    draft = f"{COMPOSER_DRAFT_SENTINEL}\ndraft"
    trace = f"{REASONING_TRACE_SENTINEL}\ntrace"
    baseline = build_reviewer_prompt(
        staged_draft_body=draft,
        reasoning_trace_body=trace,
    )
    observer = build_generate_lane_reviewer_prompt(
        packet_text="services/foo.py",
        staged_draft_body=draft,
        reasoning_trace_body=trace,
    )
    assert observer.startswith(baseline)
    assert not observer.startswith(_DENSIFY_REVIEWER_EMPTY_SCOPE_PREFIX)


@pytest.mark.asyncio
async def test_partial_default_on_event_reviewer_model_is_spec_default() -> None:
    body = DensifyCandidateReadyBody(
        draft_adequacy="partial",
        staged_draft_uri="workspaces:spec.md",
        reasoning_trace_uri="workspaces:spec.md#reasoning_trace",
        parent_dispatch_thread_id="thread:1805",
        density_triage="dispatch_surface",
        staged_draft_body=f"{COMPOSER_DRAFT_SENTINEL}\n# staged spec",
        reasoning_trace_body=f"{REASONING_TRACE_SENTINEL}\n# trace",
    )
    events: list[FrontierDensifyReviewAdmitted] = []

    with patch(
        "systems.frontier_consult.densify_candidate_ready.spawn_densify_reviewer_child",
        new_callable=AsyncMock,
        return_value={"execution_id": "exec-review-1"},
    ):
        await handle_densify_candidate_ready(
            request_id="req-model",
            body=body,
            response=Response(),
            event_publisher=events.append,
        )

    assert len(events) == 1
    assert events[0].payload["reviewer_model"] == "cursor/composer-2.5"
