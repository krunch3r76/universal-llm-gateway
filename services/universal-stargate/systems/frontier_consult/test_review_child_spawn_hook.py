"""Tests for generate-lane auto review-child spawn hook."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from systems.frontier_consult.densify_candidate_ready import build_reviewer_prompt
from systems.frontier_consult.generate_admission_context_store import (
    AdmissionContext,
    read_admission_context,
    read_spawn_state,
    reset_generate_admission_stores_for_tests,
    try_claim_spawn_pending,
    write_admission_context,
)
from systems.frontier_consult.packet_review_surface import (
    FOOTER_BY_SURFACE,
    NEGATIVE_SPACE_BY_SURFACE,
)
from systems.frontier_consult.review_child_spawn_hook import (
    _SPAWN_PROVENANCE,
    _build_generate_lane_review_prompt,
    _ReviewPromptBuild,
    handle_worker_completed_event,
    is_review_child_execution,
    reset_review_child_spawn_hook_for_tests,
    resolve_executor_family,
    select_cross_family_reviewer,
    should_spawn_review_child,
)
from systems.frontier_consult.skill_suggest_durable_state import DurableTerminalEvent


def _terminal_stub(**kwargs: object) -> DurableTerminalEvent:
    return DurableTerminalEvent(
        signal="frontier.sdk.worker.completed",
        dispatch_id=str(kwargs.get("dispatch_id") or "disp-1"),
        thread_id=str(kwargs.get("thread_id") or "thread:worker"),
        execution_id=str(kwargs.get("execution_id") or "exec"),
        payload={},
    )


@pytest.fixture(autouse=True)
def _clean_stores() -> None:
    reset_generate_admission_stores_for_tests()
    reset_review_child_spawn_hook_for_tests()
    yield
    reset_generate_admission_stores_for_tests()
    reset_review_child_spawn_hook_for_tests()


def _write_generate_ctx(
    execution_id: str,
    *,
    auto_review_child: bool = True,
    resolved_model: str = "cursor/claude-sonnet-5",
    suppress_review_spawn: bool = False,
    review_surface: str | None = None,
    dispatch_lane: str | None = None,
) -> None:
    write_admission_context(
        execution_id=execution_id,
        auto_review_child=auto_review_child,
        op="generate",
        role="cursor-sdk",
        resolved_model=resolved_model,
        parent_dispatch_thread_id="thread:parent",
        dispatch_thread_id="thread:parent",
        review_surface=review_surface,
        dispatch_lane=dispatch_lane,
        suppress_review_spawn=suppress_review_spawn,
    )


def test_d7_admission_context_round_trip() -> None:
    _write_generate_ctx("exec-ctx-1")
    ctx = read_admission_context("exec-ctx-1")
    assert ctx is not None
    assert ctx.auto_review_child is True
    assert ctx.op == "generate"
    assert ctx.role == "cursor-sdk"


def test_d3_cross_family_openai_executor_gets_cursor_opus() -> None:
    sel = select_cross_family_reviewer("openai/gpt-5.5")
    assert sel is not None
    assert sel.model == "cursor/claude-opus-5"
    assert sel.family == "anthropic"


def test_d3_cross_family_cursor_executor_gets_openai() -> None:
    sel = select_cross_family_reviewer("cursor/claude-sonnet-5")
    assert sel is not None
    # Anthropic-family executor → check/review default (terra = openai-family).
    assert sel.model == "cursor/gpt-5.6-terra"
    assert sel.family == "openai"


def test_cdp_fable_executor_does_not_echo_substrate_as_family() -> None:
    """cdp/fable is anthropic-family; reviewer must be a measured other family."""
    assert resolve_executor_family("cdp/fable") == "anthropic"
    sel = select_cross_family_reviewer("cdp/fable")
    assert sel is not None
    assert sel.family != "cdp"
    assert sel.family != "anthropic"


def test_d3_fail_closed_when_alternate_not_admitted() -> None:
    with patch(
        "systems.frontier_consult.review_child_spawn_hook._reviewer_model_admitted",
        return_value=False,
    ):
        assert select_cross_family_reviewer("openai/gpt-5.5") is None


def test_d2_review_child_predicate_blocks_grandchild() -> None:
    ctx = AdmissionContext(
        execution_id="child-1",
        auto_review_child=True,
        op="to_thread",
        role="reviewer",
        resolved_model="openai/gpt-5.5",
        parent_dispatch_thread_id="thread:parent",
        dispatch_thread_id="thread:parent",
        spawn_template_provenance=_SPAWN_PROVENANCE,
        created_at="2026-01-01T00:00:00+00:00",
    )
    assert is_review_child_execution(ctx) is True
    assert should_spawn_review_child(ctx) is False


def test_d2_cursor_sdk_review_child_blocks_grandchild() -> None:
    """cursor/* alternate spawns as generate/cursor-sdk — must not cascade."""
    ctx = AdmissionContext(
        execution_id="child-cursor-1",
        auto_review_child=True,
        op="generate",
        role="cursor-sdk",
        resolved_model="cursor/claude-opus-5",
        parent_dispatch_thread_id="thread:parent",
        dispatch_thread_id="thread:parent",
        spawn_template_provenance=_SPAWN_PROVENANCE,
        created_at="2026-01-01T00:00:00+00:00",
    )
    assert is_review_child_execution(ctx) is True
    assert should_spawn_review_child(ctx) is False


def test_ac6_non_child_reviewer_not_suppressed() -> None:
    ctx = AdmissionContext(
        execution_id="standalone-1",
        auto_review_child=True,
        op="to_thread",
        role="reviewer",
        resolved_model="openai/gpt-5.5",
        parent_dispatch_thread_id="thread:other",
        dispatch_thread_id="thread:other",
        spawn_template_provenance=None,
        created_at="2026-01-01T00:00:00+00:00",
    )
    assert is_review_child_execution(ctx) is False
    assert should_spawn_review_child(ctx) is False


def test_ac7_unset_auto_review_child_no_spawn() -> None:
    _write_generate_ctx("exec-no-child", auto_review_child=False)
    ctx = read_admission_context("exec-no-child")
    assert ctx is not None
    assert should_spawn_review_child(ctx) is False


@pytest.mark.asyncio
async def test_ac1_exactly_one_spawn_duplicate_none() -> None:
    _write_generate_ctx("exec-dedupe")
    spawn_mock = AsyncMock(return_value={"execution_id": "child-exec-1"})
    with patch(
        "systems.frontier_consult.review_child_spawn_hook.spawn_generate_lane_review_child",
        spawn_mock,
    ), patch(
        "systems.frontier_consult.review_child_spawn_hook.durable_catch_up_terminal",
        side_effect=_terminal_stub,
    ):
        await handle_worker_completed_event(
            execution_id="exec-dedupe",
            thread_id="thread:worker",
            dispatch_id="disp-1",
        )
        await handle_worker_completed_event(
            execution_id="exec-dedupe",
            thread_id="thread:worker",
            dispatch_id="disp-1",
        )
    assert spawn_mock.await_count == 1
    state = read_spawn_state("exec-dedupe")
    assert state is not None
    assert state.state == "final"
    assert state.review_child_execution_id == "child-exec-1"


@pytest.mark.asyncio
async def test_ac2_crash_after_pending_reconciled_once() -> None:
    _write_generate_ctx("exec-crash")
    assert try_claim_spawn_pending(
        parent_execution_id="exec-crash",
        parent_dispatch_thread_id="thread:parent",
        parent_thread_id="thread:worker",
        reviewer_model="openai/gpt-5.5",
    )
    spawn_mock = AsyncMock(return_value={"execution_id": "child-crash-1"})
    with patch(
        "systems.frontier_consult.review_child_spawn_hook.spawn_generate_lane_review_child",
        spawn_mock,
    ), patch(
        "systems.frontier_consult.review_child_spawn_hook.durable_catch_up_terminal",
        side_effect=_terminal_stub,
    ):
        from systems.frontier_consult.review_child_spawn_hook import (
            reconcile_pending_spawns,
        )

        await reconcile_pending_spawns()
        await reconcile_pending_spawns()
    assert spawn_mock.await_count == 1


@pytest.mark.asyncio
async def test_ac3_context_miss_emits_context_missing() -> None:
    published: list[str] = []
    with patch(
        "systems.frontier_consult.review_child_spawn_hook.publish_frontier_event",
        side_effect=lambda ev: published.append(ev.signal),
    ), patch(
        "systems.frontier_consult.review_child_spawn_hook.CONTEXT_MISS_MAX_ATTEMPTS",
        2,
    ), patch(
        "systems.frontier_consult.review_child_spawn_hook.CONTEXT_MISS_RETRY_SECONDS",
        0,
    ):
        await handle_worker_completed_event(
            execution_id="exec-miss",
            thread_id="thread:worker",
            dispatch_id=None,
        )
    assert "frontier.review_child.context_missing" in published


@pytest.mark.asyncio
async def test_ac3_race_resolves_after_context_write() -> None:
    published: list[str] = []

    async def _late_write_and_sleep(*args: object, **kwargs: object) -> None:
        write_admission_context(
            execution_id="exec-race",
            auto_review_child=True,
            op="generate",
            role="cursor-sdk",
            resolved_model="cursor/claude-sonnet-5",
            parent_dispatch_thread_id="thread:parent",
            dispatch_thread_id="thread:parent",
        )

    spawn_mock = AsyncMock(return_value={"execution_id": "child-race-1"})
    with patch(
        "systems.frontier_consult.review_child_spawn_hook.publish_frontier_event",
        side_effect=lambda ev: published.append(ev.signal),
    ), patch(
        "systems.frontier_consult.review_child_spawn_hook.asyncio.sleep",
        new=AsyncMock(side_effect=_late_write_and_sleep),
    ), patch(
        "systems.frontier_consult.review_child_spawn_hook.spawn_generate_lane_review_child",
        spawn_mock,
    ), patch(
        "systems.frontier_consult.review_child_spawn_hook.durable_catch_up_terminal",
        side_effect=_terminal_stub,
    ):
        await handle_worker_completed_event(
            execution_id="exec-race",
            thread_id="thread:worker",
            dispatch_id=None,
        )
    assert spawn_mock.await_count == 1
    assert "frontier.sdk.review_child.spawned" in published


@pytest.mark.asyncio
async def test_ac9_spawn_emits_review_child_spawned_event() -> None:
    _write_generate_ctx("exec-emit")
    published: list[str] = []
    with patch(
        "systems.frontier_consult.review_child_spawn_hook.publish_frontier_event",
        side_effect=lambda ev: published.append(ev.signal),
    ), patch(
        "systems.frontier_consult.review_child_spawn_hook.spawn_generate_lane_review_child",
        AsyncMock(return_value={"execution_id": "child-emit-1"}),
    ), patch(
        "systems.frontier_consult.review_child_spawn_hook.durable_catch_up_terminal",
        side_effect=_terminal_stub,
    ):
        await handle_worker_completed_event(
            execution_id="exec-emit",
            thread_id="thread:worker",
            dispatch_id="disp-1",
        )
    assert "frontier.sdk.review_child.spawned" in published


def test_ac8_wired_lane_no_spawn_path_warning() -> None:
    from systems.frontier_consult.densify_triage import build_generate_review_envelope

    env = build_generate_review_envelope(
        density_triage="judgment_required",
        review_opt_out_reason_code=None,
        auto_review_child=True,
    )
    assert "auto_review_child_warning" not in env


def test_ac10_spawn_body_read_only_light_bounded() -> None:
    from systems.frontier_consult.route import TeamDispatchToThreadBody

    body = TeamDispatchToThreadBody(
        op="to_thread",
        role="reviewer",
        dispatch_thread_id="thread:parent",
        thread="thread:parent",
        contract="light-bounded",
        model="openai/gpt-5.5",
        auto_review_child=True,
        read_only=True,
        spawn_review_provenance="generate_review_child",
    )
    assert body.read_only is True
    assert body.contract == "light-bounded"


@pytest.mark.asyncio
async def test_a24105_spawn_body_thread_is_coord() -> None:
    from systems.frontier_consult.review_child_spawn_hook import (
        ReviewerSelection,
        spawn_generate_lane_review_child,
    )
    from systems.frontier_consult.route import TeamDispatchToThreadBody

    captured: list[TeamDispatchToThreadBody] = []

    async def _capture(body: TeamDispatchToThreadBody, _resp: object) -> dict[str, str]:
        captured.append(body)
        return {"execution_id": "child-a24105"}

    write_admission_context(
        execution_id="exec-a24105b",
        auto_review_child=True,
        op="generate",
        role="cursor-sdk",
        resolved_model="cursor/composer-2.5",
        parent_dispatch_thread_id="thread:coord",
        dispatch_thread_id="thread:coord",
    )
    ctx = read_admission_context("exec-a24105b")
    assert ctx is not None
    with patch(
        "systems.frontier_consult.route.team_dispatch",
        _capture,
    ), patch(
        "systems.frontier_consult.review_child_spawn_hook._build_generate_lane_review_prompt",
        AsyncMock(
            return_value=_ReviewPromptBuild(
                prompt="review prompt",
                prompt_bind_mode="explicit_inline",
                prompt_turn_number=None,
                latest_read_outcome="skipped",
                bound_prompt_class="caller_prompt",
                bound_prompt_digest="abc:review prompt",
            )
        ),
    ):
        result = await spawn_generate_lane_review_child(
            request_id="req-a24105b",
            ctx=ctx,
            parent_thread_id="thread:worker-closed",
            reviewer=ReviewerSelection(model="openai/gpt-5.5", family="openai"),
        )
    assert result["execution_id"] == "child-a24105"
    assert len(captured) == 1
    assert captured[0].thread == "thread:coord"
    assert captured[0].dispatch_thread_id == "thread:coord"
    assert captured[0].thread != "thread:worker-closed"


@pytest.mark.asyncio
async def test_openai_executor_review_child_uses_cursor_sdk_generate() -> None:
    """OpenAI executor → cursor/opus alternate admits via seat=cursor-sdk generate."""
    from systems.frontier_consult.review_child_spawn_hook import (
        ReviewerSelection,
        spawn_generate_lane_review_child,
    )
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    captured: list[object] = []

    async def _capture(body: object, _resp: object) -> dict[str, str]:
        captured.append(body)
        return {"execution_id": "child-cursor-opus"}

    write_admission_context(
        execution_id="exec-openai-parent",
        auto_review_child=True,
        op="generate",
        role="cursor-sdk",
        resolved_model="openai/gpt-5.5",
        parent_dispatch_thread_id="thread:coord",
        dispatch_thread_id="thread:coord",
        prompt_turn_number=12,
        prompt_bind_mode="frozen_turn",
    )
    ctx = read_admission_context("exec-openai-parent")
    assert ctx is not None
    with patch(
        "systems.frontier_consult.route.team_dispatch",
        _capture,
    ), patch(
        "systems.frontier_consult.review_child_spawn_hook._build_generate_lane_review_prompt",
        AsyncMock(
            return_value=_ReviewPromptBuild(
                prompt="review prompt",
                prompt_bind_mode="frozen_turn",
                prompt_turn_number=12,
                latest_read_outcome="ok",
                bound_prompt_class="caller_prompt",
                bound_prompt_digest="abc:review prompt",
            )
        ),
    ):
        result = await spawn_generate_lane_review_child(
            request_id="req-cursor-alt",
            ctx=ctx,
            parent_thread_id="thread:worker-closed",
            reviewer=ReviewerSelection(
                model="cursor/claude-opus-5", family="cursor"
            ),
        )
    assert result["execution_id"] == "child-cursor-opus"
    assert len(captured) == 1
    body = captured[0]
    assert isinstance(body, TeamDispatchGenerateBody)
    assert body.op == "generate"
    assert body.seat == "cursor-sdk"
    assert body.model == "cursor/claude-opus-5"
    assert body.prompt == "review prompt"
    assert body.auto_review_child is False
    assert body.prompt_turn_number == 12
    assert body.prompt_bind_mode == "frozen_turn"
    child_ctx = read_admission_context("child-cursor-opus")
    assert child_ctx is not None
    assert child_ctx.spawn_template_provenance == _SPAWN_PROVENANCE
    assert child_ctx.suppress_review_spawn is True
    assert should_spawn_review_child(child_ctx) is False


@pytest.mark.asyncio
async def test_a24105_fail_closed_without_coord_thread() -> None:
    from systems.frontier_consult.review_child_spawn_hook import (
        ReviewerSelection,
        spawn_generate_lane_review_child,
    )

    write_admission_context(
        execution_id="exec-a24105c",
        auto_review_child=True,
        op="generate",
        role="cursor-sdk",
        resolved_model="cursor/composer-2.5",
        parent_dispatch_thread_id=None,
        dispatch_thread_id=None,
    )
    ctx = read_admission_context("exec-a24105c")
    assert ctx is not None
    result = await spawn_generate_lane_review_child(
        request_id="req-a24105c",
        ctx=ctx,
        parent_thread_id="thread:worker-only",
        reviewer=ReviewerSelection(model="openai/gpt-5.5", family="openai"),
    )
    assert result == {}


@pytest.mark.asyncio
async def test_generate_lane_review_prompt_includes_ac_observer_footer() -> None:
    with patch(
        "systems.frontier_consult.dispatch_thread_context.read_dispatch_thread_body_at_turn",
        AsyncMock(return_value="Brief for review."),
    ):
        build = await _build_generate_lane_review_prompt(
            request_id="req-footer",
            parent_dispatch_thread_id="thread:parent",
            prompt_turn_number=42,
            prompt_bind_mode="frozen_turn",
        )
    assert build is not None
    assert "Packet AC observer (advisory)" in build.prompt
    assert (
        "Self-check PASS is evidence to inspect, not completion authority"
        in build.prompt
    )
    assert "PASS or FAIL per packet AC" in build.prompt


_SIDECAR_PACKET = """
<scope>
Files expected: - `cortex://notes/system/specs/foo.md`
</scope>
<task_guidance>
Write deliverable to cortex://notes/system/specs/foo.md
STOP after A sidecar — ¬ implement.
</task_guidance>
<output_format>
fs(op="write", path="cortex://notes/system/specs/foo.md")
</output_format>
<mcp_capabilities>
fs(op="write", path="cortex://notes/system/specs/foo.md")
</mcp_capabilities>
<corpus>
services/universal-stargate/systems/frontier_consult/
</corpus>
"""

_SOURCE_PACKET = """
<scope>
Files expected: - `services/universal-stargate/systems/foo.py`
</scope>
<task_guidance>
Implement production change.
</task_guidance>
"""


@pytest.mark.asyncio
async def test_generate_lane_prompt_sidecar_surface() -> None:
    with patch(
        "systems.frontier_consult.dispatch_thread_context.read_dispatch_thread_body_at_turn",
        AsyncMock(return_value=_SIDECAR_PACKET),
    ):
        build = await _build_generate_lane_review_prompt(
            request_id="req-sidecar",
            parent_dispatch_thread_id="thread:sidecar",
            prompt_turn_number=7,
            prompt_bind_mode="frozen_turn",
        )
    assert build is not None
    prompt = build.prompt
    assert NEGATIVE_SPACE_BY_SURFACE["sidecar"] in prompt
    assert FOOTER_BY_SURFACE["sidecar"] in prompt
    assert "source: N/A" in prompt
    assert "resulting source and tests" not in prompt


@pytest.mark.asyncio
async def test_generate_lane_prompt_source_surface() -> None:
    with patch(
        "systems.frontier_consult.dispatch_thread_context.read_dispatch_thread_body_at_turn",
        AsyncMock(return_value=_SOURCE_PACKET),
    ):
        build = await _build_generate_lane_review_prompt(
            request_id="req-source",
            parent_dispatch_thread_id="thread:source",
            prompt_turn_number=11,
            prompt_bind_mode="frozen_turn",
        )
    assert build is not None
    prompt = build.prompt
    assert "resulting source and tests" in prompt
    assert "source: N/A" not in prompt


def test_densify_build_reviewer_prompt_default_byte_identical() -> None:
    draft = "draft body"
    trace = "trace body"
    golden = (
        "<negative_space>Per new/changed param or branch: where invalid + does the "
        "spec reject it there with a test?</negative_space>\n"
        f"<composer_draft>\n{draft}\n</composer_draft>\n"
        f"<reasoning_trace>\n{trace}\n</reasoning_trace>"
    )
    assert (
        build_reviewer_prompt(staged_draft_body=draft, reasoning_trace_body=trace)
        == golden
    )


def test_render_and_diff_sidecar_vs_source_prompts() -> None:
    draft = "draft"
    trace = "trace"
    from systems.frontier_consult.light_bounded_ac_observer import (
        build_generate_lane_reviewer_prompt,
    )

    sidecar_prompt = build_generate_lane_reviewer_prompt(
        packet_text=_SIDECAR_PACKET,
        staged_draft_body=draft,
        reasoning_trace_body=trace,
    )
    source_prompt = build_generate_lane_reviewer_prompt(
        packet_text=_SOURCE_PACKET,
        staged_draft_body=draft,
        reasoning_trace_body=trace,
    )
    assert sidecar_prompt != source_prompt
    assert NEGATIVE_SPACE_BY_SURFACE["sidecar"] in sidecar_prompt
    assert NEGATIVE_SPACE_BY_SURFACE["source"] in source_prompt
    assert FOOTER_BY_SURFACE["sidecar"] in sidecar_prompt
    assert FOOTER_BY_SURFACE["source"] in source_prompt


def test_resolve_executor_family_openai() -> None:
    assert resolve_executor_family("openai/gpt-5.5") == "openai"


def test_d4_pending_claim_idempotent() -> None:
    assert try_claim_spawn_pending(
        parent_execution_id="exec-claim",
        parent_dispatch_thread_id="thread:parent",
        parent_thread_id="thread:worker",
        reviewer_model="openai/gpt-5.5",
    )
    assert not try_claim_spawn_pending(
        parent_execution_id="exec-claim",
        parent_dispatch_thread_id="thread:parent",
        parent_thread_id="thread:worker",
        reviewer_model="openai/gpt-5.5",
    )


def test_suppress_review_spawn_blocks_spawn() -> None:
    _write_generate_ctx(
        "exec-suppress",
        suppress_review_spawn=True,
        review_surface="sidecar",
        dispatch_lane="path-sim-admit-gate",
    )
    ctx = read_admission_context("exec-suppress")
    assert ctx is not None
    assert ctx.auto_review_child is True
    assert should_spawn_review_child(ctx) is False


@pytest.mark.asyncio
async def test_suppressed_path_sim_stage_a_no_spawn_on_coord() -> None:
    _write_generate_ctx(
        "exec-path-sim",
        suppress_review_spawn=True,
        review_surface="sidecar",
        dispatch_lane="path-sim-admit-gate",
    )
    spawn_mock = AsyncMock(return_value={"execution_id": "child-should-not"})
    with patch(
        "systems.frontier_consult.review_child_spawn_hook.spawn_generate_lane_review_child",
        spawn_mock,
    ), patch(
        "systems.frontier_consult.review_child_spawn_hook.durable_catch_up_terminal",
        side_effect=_terminal_stub,
    ):
        await handle_worker_completed_event(
            execution_id="exec-path-sim",
            thread_id="thread:worker",
            dispatch_id="disp-1",
        )
    spawn_mock.assert_not_awaited()


def test_source_surface_lb_still_spawns_when_not_suppressed() -> None:
    _write_generate_ctx(
        "exec-source-lb",
        suppress_review_spawn=False,
        review_surface="source",
    )
    ctx = read_admission_context("exec-source-lb")
    assert ctx is not None
    assert should_spawn_review_child(ctx) is True


@pytest.mark.asyncio
async def test_a6655_spawn_fail_closed_on_frozen_read_failure() -> None:
    from systems.frontier_consult.admission import FrontierEndpointError
    from systems.frontier_consult.review_child_spawn_hook import (
        ReviewerSelection,
        spawn_generate_lane_review_child,
    )

    write_admission_context(
        execution_id="exec-fail-closed",
        auto_review_child=True,
        op="generate",
        role="cursor-sdk",
        resolved_model="cursor/claude-sonnet-5",
        parent_dispatch_thread_id="thread:coord",
        dispatch_thread_id="thread:coord",
        prompt_turn_number=2198,
        prompt_bind_mode="frozen_turn",
    )
    ctx = read_admission_context("exec-fail-closed")
    assert ctx is not None
    dispatch_mock = AsyncMock(return_value={"execution_id": "child-should-not"})
    with patch(
        "systems.frontier_consult.review_child_spawn_hook._dispatch_review_child",
        dispatch_mock,
    ), patch(
        "systems.frontier_consult.dispatch_thread_context.read_dispatch_thread_body_at_turn",
        AsyncMock(
            side_effect=FrontierEndpointError(
                request_id="req-fc",
                field="dispatch_thread_id",
                reason="not a prompt",
                status_code=422,
                code="dispatch_thread_latest_not_prompt",
            )
        ),
    ):
        result = await spawn_generate_lane_review_child(
            request_id="req-fc",
            ctx=ctx,
            parent_thread_id="thread:worker",
            reviewer=ReviewerSelection(model="openai/gpt-5.5", family="openai"),
        )
    assert result == {}
    dispatch_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_a6655_spawn_uses_frozen_turn_not_latest() -> None:
    from systems.frontier_consult.review_child_spawn_hook import (
        ReviewerSelection,
        spawn_generate_lane_review_child,
    )

    frozen_turn = 2198
    read_at = AsyncMock(return_value="Frozen brief at N.")
    read_latest = AsyncMock(side_effect=AssertionError("must not re-resolve latest"))
    write_admission_context(
        execution_id="exec-frozen",
        auto_review_child=True,
        op="generate",
        role="cursor-sdk",
        resolved_model="cursor/claude-sonnet-5",
        parent_dispatch_thread_id="6655",
        dispatch_thread_id="6655",
        prompt_turn_number=frozen_turn,
        prompt_bind_mode="frozen_turn",
    )
    ctx = read_admission_context("exec-frozen")
    assert ctx is not None
    with patch(
        "systems.frontier_consult.dispatch_thread_context.read_dispatch_thread_body_at_turn",
        read_at,
    ), patch(
        "systems.frontier_consult.dispatch_thread_context.read_latest_dispatch_thread_body",
        read_latest,
    ), patch(
        "systems.frontier_consult.review_child_spawn_hook._dispatch_review_child",
        AsyncMock(return_value={"execution_id": "child-frozen"}),
    ):
        await spawn_generate_lane_review_child(
            request_id="req-frozen",
            ctx=ctx,
            parent_thread_id="thread:worker",
            reviewer=ReviewerSelection(model="openai/gpt-5.5", family="openai"),
        )
    read_at.assert_awaited_once()
    assert read_at.await_args.kwargs["turn_number"] == frozen_turn
    read_latest.assert_not_awaited()


@pytest.mark.asyncio
async def test_a6655_prompt_bind_instrumentation_emitted() -> None:
    from systems.frontier_consult.review_child_spawn_hook import (
        ReviewerSelection,
        spawn_generate_lane_review_child,
    )

    published: list[str] = []
    write_admission_context(
        execution_id="exec-instrument",
        auto_review_child=True,
        op="generate",
        role="cursor-sdk",
        resolved_model="cursor/claude-sonnet-5",
        parent_dispatch_thread_id="6655",
        dispatch_thread_id="6655",
        prompt_turn_number=100,
        prompt_bind_mode="frozen_turn",
    )
    ctx = read_admission_context("exec-instrument")
    assert ctx is not None
    with patch(
        "systems.frontier_consult.review_child_spawn_hook.publish_frontier_event",
        side_effect=lambda ev: published.append(ev.signal),
    ), patch(
        "systems.frontier_consult.dispatch_thread_context.read_dispatch_thread_body_at_turn",
        AsyncMock(return_value="Worker thread `x` should not pass — caller brief."),
    ), patch(
        "systems.frontier_consult.review_child_spawn_hook._dispatch_review_child",
        AsyncMock(return_value={"execution_id": "child-inst"}),
    ):
        await spawn_generate_lane_review_child(
            request_id="req-inst",
            ctx=ctx,
            parent_thread_id="thread:worker",
            reviewer=ReviewerSelection(model="openai/gpt-5.5", family="openai"),
        )
    assert "frontier.review_child.prompt_bind" in published
