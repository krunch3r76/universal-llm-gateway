"""Tests for generate-lane auto review-child spawn hook."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.responses import JSONResponse
from implement_admission.check_review_substrate import (
    consultant_identity,
    is_claude_identity,
    model_identity,
)
from implement_admission.review_defaults import ReviewDefault

from systems.frontier_consult.densify_candidate_ready import build_reviewer_prompt
from systems.frontier_consult.generate_admission_context_store import (
    AdmissionContext,
    read_admission_context,
    read_spawn_state,
    reset_generate_admission_stores_for_tests,
    try_claim_spawn_pending,
    write_admission_context,
)
from systems.frontier_consult.generate_lane_ac_observer import (
    FROZEN_INSTRUCTION_MAX_BYTES,
    build_generate_lane_reviewer_prompt,
    resolve_frozen_instruction_at_admit,
)
from systems.frontier_consult.packet_review_surface import (
    FOOTER_BY_SURFACE,
    NEGATIVE_SPACE_BY_SURFACE,
)
from systems.frontier_consult.review_child_spawn_hook import (
    _REVIEWER_EMPTY_SCOPE_PREFIX,
    _SPAWN_PROVENANCE,
    ReviewerSelection,
    _build_generate_lane_review_prompt,
    _ReviewPromptBuild,
    handle_worker_completed_event,
    is_review_child_execution,
    reconcile_pending_spawns,
    reset_review_child_spawn_hook_for_tests,
    select_independent_reviewer,
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


_IMPLEMENTATION_DEFAULT = ReviewDefault(
    model="cdp/opus-5", contract="code-review", effort="high"
)
_CLAUDE_AUTHOR_DEFAULT = ReviewDefault(
    model="cursor/composer-2.5", contract="check-review", effort=None
)


def _review_default_loader(kind: str, *, policy=None) -> ReviewDefault:
    if kind == "implementation":
        return _IMPLEMENTATION_DEFAULT
    if kind == "implementation_claude_author":
        return _CLAUDE_AUTHOR_DEFAULT
    raise ValueError(f"unknown kind {kind!r}")


@pytest.fixture(autouse=True)
def _clean_stores() -> None:
    reset_generate_admission_stores_for_tests()
    reset_review_child_spawn_hook_for_tests()
    yield
    reset_generate_admission_stores_for_tests()
    reset_review_child_spawn_hook_for_tests()


@pytest.fixture(autouse=True)
def _patch_review_defaults_loader() -> None:
    with patch(
        "systems.frontier_consult.review_child_spawn_hook._load_review_default",
        side_effect=_review_default_loader,
    ):
        yield


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


def test_ac_b1_openai_executor_selects_cdp_implementation_default() -> None:
    sel = select_independent_reviewer("openai/gpt-5.5")
    assert sel is not None
    assert sel.model == "cdp/opus-5"
    assert sel.contract == "code-review"
    assert sel.identity.model_identity == "claude-opus-5"


def test_ac_b2_claude_executor_selects_composer_check_review() -> None:
    sel = select_independent_reviewer("cursor/claude-sonnet-5")
    assert sel is not None
    assert sel.model == "cursor/composer-2.5"
    assert sel.contract == "check-review"
    assert sel.identity.model_identity == "composer-2.5"


def test_ac_b2_second_claude_model_uses_claude_author_kind() -> None:
    sel = select_independent_reviewer("cdp/opus-5")
    assert sel is not None
    assert sel.model == "cursor/composer-2.5"


def test_check1_composer_executor_selects_cdp_implementation_default() -> None:
    sel = select_independent_reviewer("cursor/composer-2.5")
    assert sel is not None
    assert sel.model == "cdp/opus-5"
    assert sel.contract == "code-review"


def test_cdp_fable_executor_gets_independent_reviewer() -> None:
    """cdp/fable executor must not echo substrate; reviewer is independent."""
    sel = select_independent_reviewer("cdp/fable")
    assert sel is not None
    assert sel.identity.model_identity != "fable"
    exec_id = consultant_identity("cdp/fable", None)
    assert sel.identity.model_identity != exec_id.model_identity or (
        sel.identity.rung != exec_id.rung
    )


def test_d3_same_model_same_rung_fail_closed() -> None:
    same = ReviewDefault(
        model="cursor/claude-sonnet-5", contract="check-review", effort=None
    )
    with patch(
        "systems.frontier_consult.review_child_spawn_hook._load_review_default",
        return_value=same,
    ):
        assert select_independent_reviewer("cursor/claude-sonnet-5") is None


def test_d3_fail_closed_when_alternate_not_admitted() -> None:
    with patch(
        "systems.frontier_consult.review_child_spawn_hook._reviewer_model_admitted",
        return_value=False,
    ):
        assert select_independent_reviewer("openai/gpt-5.5") is None


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
    with (
        patch(
            "systems.frontier_consult.review_child_spawn_hook.spawn_generate_lane_review_child",
            spawn_mock,
        ),
        patch(
            "systems.frontier_consult.review_child_spawn_hook.durable_catch_up_terminal",
            side_effect=_terminal_stub,
        ),
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
    with (
        patch(
            "systems.frontier_consult.review_child_spawn_hook.spawn_generate_lane_review_child",
            spawn_mock,
        ),
        patch(
            "systems.frontier_consult.review_child_spawn_hook.durable_catch_up_terminal",
            side_effect=_terminal_stub,
        ),
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
    with (
        patch(
            "systems.frontier_consult.review_child_spawn_hook.publish_frontier_event",
            side_effect=lambda ev: published.append(ev.signal),
        ),
        patch(
            "systems.frontier_consult.review_child_spawn_hook.CONTEXT_MISS_MAX_ATTEMPTS",
            2,
        ),
        patch(
            "systems.frontier_consult.review_child_spawn_hook.CONTEXT_MISS_RETRY_SECONDS",
            0,
        ),
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
    with (
        patch(
            "systems.frontier_consult.review_child_spawn_hook.publish_frontier_event",
            side_effect=lambda ev: published.append(ev.signal),
        ),
        patch(
            "systems.frontier_consult.review_child_spawn_hook.asyncio.sleep",
            new=AsyncMock(side_effect=_late_write_and_sleep),
        ),
        patch(
            "systems.frontier_consult.review_child_spawn_hook.spawn_generate_lane_review_child",
            spawn_mock,
        ),
        patch(
            "systems.frontier_consult.review_child_spawn_hook.durable_catch_up_terminal",
            side_effect=_terminal_stub,
        ),
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
    with (
        patch(
            "systems.frontier_consult.review_child_spawn_hook.publish_frontier_event",
            side_effect=lambda ev: published.append(ev.signal),
        ),
        patch(
            "systems.frontier_consult.review_child_spawn_hook.spawn_generate_lane_review_child",
            AsyncMock(return_value={"execution_id": "child-emit-1"}),
        ),
        patch(
            "systems.frontier_consult.review_child_spawn_hook.durable_catch_up_terminal",
            side_effect=_terminal_stub,
        ),
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


def test_ac10_spawn_body_read_only_residual() -> None:
    from systems.frontier_consult.route import TeamDispatchToThreadBody

    body = TeamDispatchToThreadBody(
        op="to_thread",
        role="gatherer",
        dispatch_thread_id="thread:parent",
        thread="thread:parent",
        job="freeform",
        model="openai/gpt-5.5",
        auto_review_child=True,
        read_only=True,
        spawn_review_provenance="generate_review_child",
    )
    assert body.read_only is True
    assert body.job == "freeform"


@pytest.mark.asyncio
async def test_a24105_spawn_body_thread_is_coord() -> None:
    from systems.frontier_consult.review_child_spawn_hook import (
        spawn_generate_lane_review_child,
    )

    captured: list[object] = []

    async def _capture(body: object, _resp: object) -> dict[str, str]:
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
    with (
        patch(
            "systems.frontier_consult.route.team_dispatch",
            _capture,
        ),
        patch(
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
        ),
    ):
        result = await spawn_generate_lane_review_child(
            request_id="req-a24105b",
            ctx=ctx,
            parent_thread_id="thread:worker-closed",
            reviewer=ReviewerSelection(
                model="openai/gpt-5.5",
                identity=consultant_identity("openai/gpt-5.5", None),
            ),
        )
    assert result == {}
    assert captured == []


@pytest.mark.asyncio
async def test_openai_executor_review_child_dispatches_cdp_generate() -> None:
    """OpenAI executor → CDP implementation reviewer (AC-B3)."""
    from systems.frontier_consult.review_child_spawn_hook import (
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
        caller_agent="cursor-sdk-lane",
        work_key="todo:review-defaults",
    )
    ctx = read_admission_context("exec-openai-parent")
    assert ctx is not None
    with (
        patch(
            "systems.frontier_consult.route.team_dispatch",
            _capture,
        ),
        patch(
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
        ),
    ):
        result = await spawn_generate_lane_review_child(
            request_id="req-cursor-alt",
            ctx=ctx,
            parent_thread_id="thread:worker-closed",
            reviewer=ReviewerSelection(
                model="cdp/opus-5",
                identity=consultant_identity("cdp/opus-5", None),
                contract="code-review",
                effort="high",
            ),
        )
    assert result["execution_id"] == "child-cursor-opus"
    assert len(captured) == 1
    body = captured[0]
    assert isinstance(body, TeamDispatchGenerateBody)
    assert body.op == "generate"
    assert body.seat is None
    assert body.lane is None
    assert body.model == "cdp/opus-5"
    assert body.job == "code-review"
    assert body.caller_agent == "cursor-sdk-lane"
    assert body.work_key == "todo:review-defaults"
    assert body.reasoning_effort == "high"
    assert body.prompt == "review prompt"
    assert body.auto_review_child is False


@pytest.mark.asyncio
async def test_ac_b3_cdp_work_key_falls_back_to_agent_bus_thread() -> None:
    from systems.frontier_consult.review_child_spawn_hook import (
        spawn_generate_lane_review_child,
    )

    captured: list[object] = []

    async def _capture(body: object, _resp: object) -> dict[str, str]:
        captured.append(body)
        return {"execution_id": "child-wk-fallback"}

    write_admission_context(
        execution_id="exec-wk-fallback",
        auto_review_child=True,
        op="generate",
        role="cursor-sdk",
        resolved_model="openai/gpt-5.5",
        parent_dispatch_thread_id="thread:coord",
        dispatch_thread_id="thread:coord",
        prompt_turn_number=7,
        prompt_bind_mode="frozen_turn",
        caller_agent="cursor-sdk-lane",
        work_key=None,
    )
    ctx = read_admission_context("exec-wk-fallback")
    assert ctx is not None
    with (
        patch("systems.frontier_consult.route.team_dispatch", _capture),
        patch(
            "systems.frontier_consult.review_child_spawn_hook._build_generate_lane_review_prompt",
            AsyncMock(
                return_value=_ReviewPromptBuild(
                    prompt="wk prompt",
                    prompt_bind_mode="frozen_turn",
                    prompt_turn_number=7,
                    latest_read_outcome="ok",
                    bound_prompt_class="caller_prompt",
                    bound_prompt_digest="x",
                )
            ),
        ),
    ):
        await spawn_generate_lane_review_child(
            request_id="req-wk",
            ctx=ctx,
            parent_thread_id="thread:worker",
            reviewer=ReviewerSelection(
                model="cdp/opus-5",
                identity=consultant_identity("cdp/opus-5", None),
                contract="code-review",
                effort="high",
            ),
        )
    assert len(captured) == 1
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    body = captured[0]
    assert isinstance(body, TeamDispatchGenerateBody)
    assert body.work_key == "agent-bus:thread:coord"


@pytest.mark.asyncio
async def test_d7_cdp_reviewer_fail_closed_without_frozen_turn() -> None:
    from systems.frontier_consult.review_child_spawn_hook import (
        spawn_generate_lane_review_child,
    )

    captured: list[object] = []

    async def _capture(body: object, _resp: object) -> dict[str, str]:
        captured.append(body)
        return {"execution_id": "child-should-not"}

    write_admission_context(
        execution_id="exec-cdp-no-pin",
        auto_review_child=True,
        op="generate",
        role="cursor-sdk",
        resolved_model="openai/gpt-5.5",
        parent_dispatch_thread_id="thread:coord",
        dispatch_thread_id="thread:coord",
        caller_agent="cursor-sdk-lane",
        prompt_turn_number=None,
        prompt_bind_mode="sentinel_fallback",
    )
    ctx = read_admission_context("exec-cdp-no-pin")
    assert ctx is not None
    with patch("systems.frontier_consult.route.team_dispatch", _capture):
        result = await spawn_generate_lane_review_child(
            request_id="req-no-pin",
            ctx=ctx,
            parent_thread_id="thread:worker",
            reviewer=ReviewerSelection(
                model="cdp/opus-5",
                identity=consultant_identity("cdp/opus-5", None),
                contract="code-review",
                effort="high",
            ),
        )
    assert result == {}
    assert captured == []


@pytest.mark.asyncio
async def test_a24105_fail_closed_without_coord_thread() -> None:
    from systems.frontier_consult.review_child_spawn_hook import (
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
        reviewer=ReviewerSelection(
            model="openai/gpt-5.5",
            identity=consultant_identity("openai/gpt-5.5", None),
        ),
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
    from systems.frontier_consult.generate_lane_ac_observer import (
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
    with (
        patch(
            "systems.frontier_consult.review_child_spawn_hook.spawn_generate_lane_review_child",
            spawn_mock,
        ),
        patch(
            "systems.frontier_consult.review_child_spawn_hook.durable_catch_up_terminal",
            side_effect=_terminal_stub,
        ),
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
    with (
        patch(
            "systems.frontier_consult.review_child_spawn_hook._dispatch_review_child",
            dispatch_mock,
        ),
        patch(
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
        ),
    ):
        result = await spawn_generate_lane_review_child(
            request_id="req-fc",
            ctx=ctx,
            parent_thread_id="thread:worker",
            reviewer=ReviewerSelection(
                model="openai/gpt-5.5",
                identity=consultant_identity("openai/gpt-5.5", None),
            ),
        )
    assert result == {}
    dispatch_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_a6655_spawn_uses_frozen_turn_not_latest() -> None:
    from systems.frontier_consult.review_child_spawn_hook import (
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
    with (
        patch(
            "systems.frontier_consult.dispatch_thread_context.read_dispatch_thread_body_at_turn",
            read_at,
        ),
        patch(
            "systems.frontier_consult.dispatch_thread_context.read_latest_dispatch_thread_body",
            read_latest,
        ),
        patch(
            "systems.frontier_consult.review_child_spawn_hook._dispatch_review_child",
            AsyncMock(return_value={"execution_id": "child-frozen"}),
        ),
    ):
        await spawn_generate_lane_review_child(
            request_id="req-frozen",
            ctx=ctx,
            parent_thread_id="thread:worker",
            reviewer=ReviewerSelection(
                model="openai/gpt-5.5",
                identity=consultant_identity("openai/gpt-5.5", None),
            ),
        )
    read_at.assert_awaited_once()
    assert read_at.await_args.kwargs["turn_number"] == frozen_turn
    read_latest.assert_not_awaited()


@pytest.mark.asyncio
async def test_a6655_prompt_bind_instrumentation_emitted() -> None:
    from systems.frontier_consult.review_child_spawn_hook import (
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
    with (
        patch(
            "systems.frontier_consult.review_child_spawn_hook.publish_frontier_event",
            side_effect=lambda ev: published.append(ev.signal),
        ),
        patch(
            "systems.frontier_consult.dispatch_thread_context.read_dispatch_thread_body_at_turn",
            AsyncMock(return_value="Worker thread `x` should not pass — caller brief."),
        ),
        patch(
            "systems.frontier_consult.review_child_spawn_hook._dispatch_review_child",
            AsyncMock(return_value={"execution_id": "child-inst"}),
        ),
    ):
        await spawn_generate_lane_review_child(
            request_id="req-inst",
            ctx=ctx,
            parent_thread_id="thread:worker",
            reviewer=ReviewerSelection(
                model="openai/gpt-5.5",
                identity=consultant_identity("openai/gpt-5.5", None),
            ),
        )
    assert "frontier.review_child.prompt_bind" in published


def test_check1_is_claude_identity_predicate() -> None:
    assert is_claude_identity(model_identity("cursor/claude-sonnet-5")) is True
    assert is_claude_identity(model_identity("cursor/claude-opus-5")) is True
    assert is_claude_identity(model_identity("cursor/composer-2.5")) is False
    assert is_claude_identity(model_identity("openai/gpt-5.5")) is False
    assert is_claude_identity(model_identity("unknown/foo")) is False


def test_check1_unknown_executor_fail_closed() -> None:
    assert select_independent_reviewer("unknown/foo") is None


def test_check7_route_policy_review_defaults_three_kinds() -> None:
    from implement_admission.review_defaults import load_review_default
    from implement_admission.routing import load_route_policy

    policy = load_route_policy()
    assert load_review_default("implementation", policy=policy).model == "cdp/opus-5"
    assert (
        load_review_default("implementation_claude_author", policy=policy).model
        == "cursor/composer-2.5"
    )
    assert load_review_default("spec", policy=policy).model == "cursor/composer-2.5"


@pytest.mark.asyncio
async def test_ac_b4_cdp_422_logs_and_single_dispatch() -> None:
    from systems.frontier_consult.review_child_spawn_hook import (
        _attempt_spawn_for_completion,
    )

    _write_generate_ctx(
        "exec-cdp-422",
        resolved_model="openai/gpt-5.5",
    )
    write_admission_context(
        execution_id="exec-cdp-422",
        auto_review_child=True,
        op="generate",
        role="cursor-sdk",
        resolved_model="openai/gpt-5.5",
        parent_dispatch_thread_id="thread:parent",
        dispatch_thread_id="thread:parent",
        caller_agent="cursor-sdk-lane",
        work_key="todo:wk",
        prompt_turn_number=3,
        prompt_bind_mode="frozen_turn",
    )
    ctx = read_admission_context("exec-cdp-422")
    assert ctx is not None
    dispatch_mock = AsyncMock(
        return_value=JSONResponse(
            status_code=422,
            content={"code": "cdp_caller_agent_required"},
        )
    )
    with (
        patch(
            "systems.frontier_consult.review_child_spawn_hook._build_generate_lane_review_prompt",
            AsyncMock(
                return_value=_ReviewPromptBuild(
                    prompt="review",
                    prompt_bind_mode="frozen_turn",
                    prompt_turn_number=3,
                    latest_read_outcome="ok",
                    bound_prompt_class="caller_prompt",
                    bound_prompt_digest="x",
                )
            ),
        ),
        patch(
            "systems.frontier_consult.route.team_dispatch",
            dispatch_mock,
        ),
        patch(
            "systems.frontier_consult.review_child_spawn_hook.logger",
            MagicMock(),
        ) as log_mock,
    ):
        terminal = _terminal_stub(execution_id="exec-cdp-422")
        assert await _attempt_spawn_for_completion(terminal=terminal, ctx=ctx) is False
    assert dispatch_mock.await_count == 1
    refused_logs = [
        c
        for c in log_mock.warning.call_args_list
        if c.args and "dispatch refused" in str(c.args[0])
    ]
    assert refused_logs
    refused = refused_logs[0]
    assert 422 in refused.args
    assert "cdp_caller_agent_required" in refused.args
    state = read_spawn_state("exec-cdp-422")
    assert state is not None
    assert state.state == "pending"


@pytest.mark.asyncio
async def test_ac_b5_cursor_child_lane_b_admit_shape() -> None:
    from implement_admission.admission_read import frontmatter_list_value
    from services.git_integration_worker.cursor_sdk_lane_select import select_lane
    from services.git_integration_worker.cursor_sdk_work_key_gate import (
        compute_write_class,
    )
    from services.git_integration_worker.models.cursor_api import CursorDispatchRequest
    from services.git_integration_worker.routes.cursor_sdk import _effective_read_only

    from systems.frontier_consult.review_child_spawn_hook import (
        spawn_generate_lane_review_child,
    )
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    captured: list[object] = []

    async def _capture(body: object, _resp: object) -> dict[str, str]:
        captured.append(body)
        return {"execution_id": "child-lane-b"}

    write_admission_context(
        execution_id="exec-lane-b",
        auto_review_child=True,
        op="generate",
        role="cursor-sdk",
        resolved_model="cursor/claude-sonnet-5",
        parent_dispatch_thread_id="thread:parent",
        dispatch_thread_id="thread:parent",
        caller_agent="cursor-sdk",
        work_key="agent-bus:thread:parent",
        prompt_turn_number=5,
        prompt_bind_mode="frozen_turn",
    )
    ctx = read_admission_context("exec-lane-b")
    assert ctx is not None
    sel = select_independent_reviewer(ctx.resolved_model)
    assert sel is not None
    packet_with_cortex = (
        "Review cites cortex://notes/system/specs/foo.md for lane B admit.\n"
    )
    with (
        patch("systems.frontier_consult.route.team_dispatch", _capture),
        patch(
            "systems.frontier_consult.review_child_spawn_hook._build_generate_lane_review_prompt",
            AsyncMock(
                return_value=_ReviewPromptBuild(
                    prompt=packet_with_cortex,
                    prompt_bind_mode="frozen_turn",
                    prompt_turn_number=5,
                    latest_read_outcome="ok",
                    bound_prompt_class="caller_prompt",
                    bound_prompt_digest="x",
                )
            ),
        ),
    ):
        await spawn_generate_lane_review_child(
            request_id="req-lane-b",
            ctx=ctx,
            parent_thread_id="thread:worker",
            reviewer=sel,
        )
    assert len(captured) == 1
    body = captured[0]
    assert isinstance(body, TeamDispatchGenerateBody)
    assert body.lane == "B"
    assert body.read_only is True
    assert body.work_key == "agent-bus:thread:parent"
    assert body.caller_agent == "cursor-sdk"
    assert frontmatter_list_value(body.prompt or "", "files_expected") == []
    assert (body.prompt or "").startswith(_REVIEWER_EMPTY_SCOPE_PREFIX)

    req = CursorDispatchRequest.model_validate(
        {
            "thread_id": body.dispatch_thread_id,
            "model": body.model,
            "dispatch_id": "disp-ac-b5",
            "execution_id": "exec-ac-b5",
            "message": body.prompt,
            "read_only": body.read_only,
            "lane": body.lane,
            "work_key": body.work_key,
            "caller_agent": body.caller_agent,
        }
    )
    contract = str(body.job or "check-review")
    assert _effective_read_only(req, contract) is True
    assert compute_write_class(
        read_only=bool(body.read_only),
        lane=body.lane,
        contract=contract,
    )
    assert body.work_key
    repo_root = Path(__file__).resolve().parents[4]
    lane, _, reason = select_lane(
        req=req,
        regime_active=True,
        source_repo=repo_root,
        files_expected=[],
        contract=contract,
    )
    assert lane == "B"
    assert reason in ("explicit", "lane_bound", "contract_regime", "nest_inherit")


async def _capture_cdp_spawn_body(
    *,
    execution_id: str,
    work_key: str | None,
    delivery_thread: str = "thread:coord",
):
    from systems.frontier_consult.review_child_spawn_hook import (
        spawn_generate_lane_review_child,
    )
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    captured: list[object] = []

    async def _capture(body: object, _resp: object) -> dict[str, str]:
        captured.append(body)
        return {"execution_id": "child-b6"}

    write_admission_context(
        execution_id=execution_id,
        auto_review_child=True,
        op="generate",
        role="cursor-sdk",
        resolved_model="openai/gpt-5.5",
        parent_dispatch_thread_id=delivery_thread,
        dispatch_thread_id=delivery_thread,
        prompt_turn_number=3,
        prompt_bind_mode="frozen_turn",
        caller_agent="cursor-sdk-lane",
        work_key=work_key,
    )
    ctx = read_admission_context(execution_id)
    assert ctx is not None
    with (
        patch("systems.frontier_consult.route.team_dispatch", _capture),
        patch(
            "systems.frontier_consult.review_child_spawn_hook._build_generate_lane_review_prompt",
            AsyncMock(
                return_value=_ReviewPromptBuild(
                    prompt="cdp review",
                    prompt_bind_mode="frozen_turn",
                    prompt_turn_number=3,
                    latest_read_outcome="ok",
                    bound_prompt_class="caller_prompt",
                    bound_prompt_digest="x",
                )
            ),
        ),
    ):
        await spawn_generate_lane_review_child(
            request_id=f"req-{execution_id}",
            ctx=ctx,
            parent_thread_id="thread:worker",
            reviewer=ReviewerSelection(
                model="cdp/opus-5",
                identity=consultant_identity("cdp/opus-5", None),
                contract="code-review",
                effort="high",
            ),
        )
    assert len(captured) == 1
    body = captured[0]
    assert isinstance(body, TeamDispatchGenerateBody)
    return body


@pytest.mark.asyncio
async def test_ac_b6_cdp_body_passes_identity_validators() -> None:
    from systems.frontier_consult.cdp_admit_identity import (
        require_cdp_caller_agent,
        require_cdp_review_work_key,
        validate_work_key_if_supplied,
    )

    for exec_id, parent_wk, expected_wk in (
        ("exec-b6-parent-wk", "todo:leg-b", "todo:leg-b"),
        ("exec-b6-fallback-wk", None, "agent-bus:thread:coord"),
    ):
        body = await _capture_cdp_spawn_body(
            execution_id=exec_id,
            work_key=parent_wk,
            delivery_thread="thread:coord",
        )
        assert body.work_key == expected_wk
        assert require_cdp_caller_agent(body, request_id="req-b6") == "cursor-sdk-lane"
        assert (
            require_cdp_review_work_key(body, "code-review", request_id="req-b6")
            is None
        )
        assert validate_work_key_if_supplied(body, request_id="req-b6") == expected_wk


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("caller_agent", "exec_id"),
    [
        (None, "exec-no-caller-none"),
        ("", "exec-no-caller-empty"),
        ("   ", "exec-no-caller-ws"),
    ],
)
async def test_ac_b8_abandon_when_caller_missing(
    caller_agent: str | None, exec_id: str
) -> None:
    from systems.frontier_consult.review_child_spawn_hook import (
        _attempt_spawn_for_completion,
    )

    write_admission_context(
        execution_id=exec_id,
        auto_review_child=True,
        op="generate",
        role="cursor-sdk",
        resolved_model="openai/gpt-5.5",
        parent_dispatch_thread_id="thread:parent",
        dispatch_thread_id="thread:parent",
        caller_agent=caller_agent,
        work_key="todo:wk",
        prompt_turn_number=2,
        prompt_bind_mode="frozen_turn",
    )
    try_claim_spawn_pending(
        parent_execution_id=exec_id,
        parent_dispatch_thread_id="thread:parent",
        parent_thread_id="thread:worker",
        reviewer_model="cdp/opus-5",
    )
    ctx = read_admission_context(exec_id)
    assert ctx is not None
    dispatch_mock = AsyncMock(return_value={"execution_id": "child-x"})
    with (
        patch(
            "systems.frontier_consult.route.team_dispatch",
            dispatch_mock,
        ),
        patch(
            "systems.frontier_consult.review_child_spawn_hook._build_generate_lane_review_prompt",
            AsyncMock(
                return_value=_ReviewPromptBuild(
                    prompt="p",
                    prompt_bind_mode="frozen_turn",
                    prompt_turn_number=2,
                    latest_read_outcome="ok",
                    bound_prompt_class="caller_prompt",
                    bound_prompt_digest="x",
                )
            ),
        ),
    ):
        terminal = _terminal_stub(execution_id=exec_id)
        assert await _attempt_spawn_for_completion(terminal=terminal, ctx=ctx) is False
    dispatch_mock.assert_not_awaited()
    state = read_spawn_state(exec_id)
    assert state is not None
    assert state.state == "final"
    assert state.review_child_execution_id is None


@pytest.mark.asyncio
async def test_b_fix_r2_missing_frozen_turn_abandon_stops_reconcile() -> None:
    import json

    from systems.frontier_consult.review_child_spawn_hook import (
        reconcile_pending_spawns,
    )

    exec_id = "exec-b-fix-r2"
    write_admission_context(
        execution_id=exec_id,
        auto_review_child=True,
        op="generate",
        role="cursor-sdk",
        resolved_model="openai/gpt-5.5",
        parent_dispatch_thread_id="thread:coord",
        dispatch_thread_id="thread:coord",
        caller_agent="cursor-sdk-lane",
        work_key="todo:wk",
        prompt_turn_number=None,
        prompt_bind_mode="explicit_external",
    )
    try_claim_spawn_pending(
        parent_execution_id=exec_id,
        parent_dispatch_thread_id="thread:coord",
        parent_thread_id="thread:worker",
        reviewer_model="cdp/opus-5",
    )
    published: list[str] = []
    with (
        patch(
            "systems.frontier_consult.review_child_spawn_hook.publish_frontier_event",
            side_effect=lambda ev: published.append(ev.signal),
        ),
        patch(
            "systems.frontier_consult.review_child_spawn_hook.durable_catch_up_terminal",
            side_effect=_terminal_stub,
        ),
        patch(
            "systems.frontier_consult.route.team_dispatch",
            AsyncMock(return_value={"execution_id": "child-should-not"}),
        ),
    ):
        await reconcile_pending_spawns()
        await reconcile_pending_spawns()
    state = read_spawn_state(exec_id)
    assert state is not None
    assert state.state == "final"
    assert state.review_child_execution_id is None
    meta = json.loads(state.attempt_metadata)
    assert meta["abandon_reason"] == "missing_frozen_turn_pin"
    assert published.count("frontier.review_child.prompt_bind") == 1


@pytest.mark.asyncio
async def test_ac_b8_abandoned_parent_not_reconciled() -> None:
    from systems.frontier_consult.review_child_spawn_hook import (
        _attempt_spawn_for_completion,
    )

    write_admission_context(
        execution_id="exec-abandon-reconcile",
        auto_review_child=True,
        op="generate",
        role="cursor-sdk",
        resolved_model="openai/gpt-5.5",
        parent_dispatch_thread_id="thread:parent",
        dispatch_thread_id="thread:parent",
        caller_agent=None,
        work_key="todo:wk",
        prompt_turn_number=2,
        prompt_bind_mode="frozen_turn",
    )
    try_claim_spawn_pending(
        parent_execution_id="exec-abandon-reconcile",
        parent_dispatch_thread_id="thread:parent",
        parent_thread_id="thread:worker",
        reviewer_model="cdp/opus-5",
    )
    ctx = read_admission_context("exec-abandon-reconcile")
    assert ctx is not None
    with (
        patch(
            "systems.frontier_consult.review_child_spawn_hook._build_generate_lane_review_prompt",
            AsyncMock(
                return_value=_ReviewPromptBuild(
                    prompt="p",
                    prompt_bind_mode="frozen_turn",
                    prompt_turn_number=2,
                    latest_read_outcome="ok",
                    bound_prompt_class="caller_prompt",
                    bound_prompt_digest="x",
                )
            ),
        ),
        patch(
            "systems.frontier_consult.review_child_spawn_hook.durable_catch_up_terminal",
            side_effect=_terminal_stub,
        ),
    ):
        terminal = _terminal_stub(execution_id="exec-abandon-reconcile")
        await _attempt_spawn_for_completion(terminal=terminal, ctx=ctx)
    dispatch_mock = AsyncMock()
    with patch(
        "systems.frontier_consult.route.team_dispatch",
        dispatch_mock,
    ):
        await reconcile_pending_spawns()
        await reconcile_pending_spawns()
    dispatch_mock.assert_not_awaited()
    state = read_spawn_state("exec-abandon-reconcile")
    assert state is not None
    assert state.state == "final"
    assert state.review_child_execution_id is None


@pytest.mark.asyncio
async def test_ac_b9_parent_write_caller_agent_none_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.frontier_consult import cursor_sdk_generate_prepare as prepare_mod

    async def _create(**_kwargs: object) -> str:
        return "thread-b9"

    async def _coord(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        "systems.frontier_consult.densify_triage.validate_generate_density_intake",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.admission.enforce_check_review_substrate_admission",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_lane_ac_observer.prepare_lb_auto_review_for_generate",
        lambda **_k: (False, False, ""),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_lane_ac_observer.stamp_lb_review_spawn_fields",
        lambda **_k: ("source", None, False),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_lane_ac_observer.validate_generate_contract_packet_rules",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        prepare_mod,
        "align_cursor_knobs",
        lambda **_k: type(
            "A",
            (),
            {
                "aligned_knobs": None,
                "warnings_as_dicts": lambda self: [],
                "knob_resolution_as_dicts": lambda self: [],
            },
        )(),
    )
    monkeypatch.setattr(prepare_mod, "emit_sdk_generate_requested", lambda **_k: None)
    monkeypatch.setattr(prepare_mod, "emit_sdk_thread_created", lambda **_k: None)
    monkeypatch.setattr(prepare_mod, "create_handoff_thread", _create)
    monkeypatch.setattr(
        prepare_mod, "admit_handoff_dispatch", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(prepare_mod, "post_coord_admit_pointer", _coord)
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate_prepare_close.complete_cursor_sdk_generate_prepare",
        AsyncMock(
            return_value=type(
                "H",
                (),
                {
                    "thread_id": "thread-b9",
                    "prompt_bind_mode": "explicit_inline",
                },
            )()
        ),
    )
    await prepare_mod.prepare_cursor_sdk_generate(
        request_id="req-b9",
        role="cursor-sdk",
        model="cursor/composer-2.5",
        subject="s",
        caller_agent=None,
        contract="none",
        packet_path=None,
        message_text="b9",
        parent_dispatch_thread_id="thread:b9-parent",
        dispatch_thread_id="thread:b9-worker",
        prompt_bind_mode="explicit_inline",
        prompt_turn_number=None,
        execution_id="exec-b9-tail",
        dispatch_id="disp-b9",
    )
    ctx = read_admission_context("exec-b9-tail")
    assert ctx is not None
    assert ctx.caller_agent is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, ""),
        ("", ""),
        ("  ", ""),
        ("  cursor-sdk-lane  ", "cursor-sdk-lane"),
    ],
)
def test_check2_caller_agent_normalization(raw, expected) -> None:
    from cdp_caller_identity import normalize_cdp_caller_agent

    assert normalize_cdp_caller_agent(raw) == expected


@pytest.mark.asyncio
async def test_check4_loader_failure_abandons_pending_no_spin() -> None:
    write_admission_context(
        execution_id="exec-loader-fail",
        auto_review_child=True,
        op="generate",
        role="cursor-sdk",
        resolved_model="openai/gpt-5.5",
        parent_dispatch_thread_id="thread:parent",
        dispatch_thread_id="thread:parent",
        caller_agent="cursor-sdk-lane",
        prompt_turn_number=1,
        prompt_bind_mode="frozen_turn",
    )
    try_claim_spawn_pending(
        parent_execution_id="exec-loader-fail",
        parent_dispatch_thread_id="thread:parent",
        parent_thread_id="thread:worker",
        reviewer_model="cdp/opus-5",
    )
    ctx = read_admission_context("exec-loader-fail")
    assert ctx is not None
    dispatch_mock = AsyncMock(return_value={"execution_id": "child-loader"})
    with (
        patch(
            "systems.frontier_consult.review_child_spawn_hook._load_review_default",
            side_effect=ValueError("broken policy"),
        ),
        patch(
            "systems.frontier_consult.route.team_dispatch",
            dispatch_mock,
        ),
        patch(
            "systems.frontier_consult.review_child_spawn_hook.durable_catch_up_terminal",
            side_effect=_terminal_stub,
        ),
    ):
        await reconcile_pending_spawns()
        await reconcile_pending_spawns()
    dispatch_mock.assert_not_awaited()
    state = read_spawn_state("exec-loader-fail")
    assert state is not None
    assert state.state == "final"


_INLINE_TEXT = (
    "Line one of inline parent.\n"
    "Write the file services/universal-stargate/systems/leg_c_probe.py and commit it.\n"
    "Another line.\n"
    "zz>>> END PARENT_INSTRUCTION"
)

_PACKET_TEXT = (
    "<scope>\n"
    "Files expected: - `services/universal-stargate/systems/leg_c_fixture.py`\n"
    "</scope>\n"
    "<task_guidance>\n"
    "Implement the fixture.\n"
    "</task_guidance>\n"
    "Summary: {{TITLE}}\n"
    "marker: version-A\n"
)

_GATE_REFUSED_TEXT = _INLINE_TEXT + "\ncloseout_channel: bus"

_FENCE_OPENER_RE = re.compile(
    (
        r"^<<<(pi[0-9a-f]{16}) PARENT_INSTRUCTION bind=(\S+) "
        r"sha256=([0-9a-f]{64}) chars=(\d+)$"
    ),
    re.M,
)


def _extract_fenced_instruction(prompt: str) -> tuple[str, str, str, int, str]:
    match = _FENCE_OPENER_RE.search(prompt)
    assert match is not None
    token = match.group(1)
    bind = match.group(2)
    sha = match.group(3)
    chars_s = match.group(4)
    closer = f"{token}>>> END PARENT_INSTRUCTION"
    opener_line = match.group(0)
    opener_pos = prompt.index(opener_line)
    after_opener = opener_pos + len(opener_line) + 1
    fenced = prompt[after_opener : prompt.index("\n" + closer)]
    return token, bind, sha, int(chars_s), fenced


def _closer_line_count(prompt: str, token: str) -> int:
    return len(
        re.findall(
            rf"^{re.escape(token)}>>> END PARENT_INSTRUCTION$",
            prompt,
            re.M,
        )
    )


def _instruction_not_in_composer_draft(prompt: str, text: str) -> bool:
    start = prompt.find("<composer_draft>")
    end = prompt.find("</composer_draft>")
    if start < 0 or end < 0:
        return True
    return text not in prompt[start:end]


async def _leg_c_admit_parent(
    monkeypatch: pytest.MonkeyPatch,
    *,
    execution_id: str,
    message_text: str | None,
    packet_path: str | None,
    tmp_path: Path | None = None,
) -> AdmissionContext:
    from systems.frontier_consult import cursor_sdk_generate_prepare as prepare_mod

    async def _create(**_kwargs: object) -> str:
        return "thread-leg-c-worker"

    async def _coord(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        "systems.frontier_consult.densify_triage.validate_generate_density_intake",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.admission.enforce_check_review_substrate_admission",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        prepare_mod,
        "align_cursor_knobs",
        lambda **_k: type(
            "A",
            (),
            {
                "aligned_knobs": None,
                "warnings_as_dicts": lambda self: [],
                "knob_resolution_as_dicts": lambda self: [],
            },
        )(),
    )
    monkeypatch.setattr(prepare_mod, "emit_sdk_generate_requested", lambda **_k: None)
    monkeypatch.setattr(prepare_mod, "emit_sdk_thread_created", lambda **_k: None)
    monkeypatch.setattr(prepare_mod, "create_handoff_thread", _create)
    monkeypatch.setattr(
        prepare_mod, "admit_handoff_dispatch", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(prepare_mod, "post_coord_admit_pointer", _coord)
    monkeypatch.setattr(
        prepare_mod,
        "probe_continuity_root_thread_id",
        AsyncMock(return_value=None),
    )
    if tmp_path is not None:
        monkeypatch.setattr(
            "systems.frontier_consult.handoff._workspaces_root",
            lambda: tmp_path,
        )

    await prepare_mod.prepare_cursor_sdk_generate(
        request_id=f"req-{execution_id}",
        role="cursor-sdk",
        model="cursor/composer-2.5",
        subject="leg-c",
        caller_agent="cursor-sdk-lane",
        contract="none",
        packet_path=packet_path,
        message_text=message_text,
        parent_dispatch_thread_id="thread:leg-c-coord",
        dispatch_thread_id="thread:leg-c-coord",
        prompt_bind_mode="explicit_external",
        prompt_turn_number=None,
        auto_review_child=True,
        execution_id=execution_id,
        dispatch_id=f"disp-{execution_id}",
    )
    ctx = read_admission_context(execution_id)
    assert ctx is not None
    return ctx


def _leg_c_write_packet(tmp_path: Path, text: str) -> str:
    packet_dir = tmp_path / "leg-c-ac"
    packet_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid4().hex}.md"
    (packet_dir / name).write_text(text, encoding="utf-8")
    return f"workspaces://leg-c-ac/{name}"


@pytest.mark.asyncio
async def test_leg_c_inline_parent_review_child_carries_frozen_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claude_bundles.cdp_review_prompt_gate import enforce_review_prompt_gate

    from systems.frontier_consult.review_child_spawn_hook import (
        spawn_generate_lane_review_child,
    )
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    exec_id = "exec-leg-c-c1"
    ctx = await _leg_c_admit_parent(
        monkeypatch,
        execution_id=exec_id,
        message_text=_INLINE_TEXT,
        packet_path=None,
    )
    captured: list[object] = []

    async def _capture(body: object, _resp: object) -> dict[str, str]:
        captured.append(body)
        return {"execution_id": "child-leg-c"}

    with patch("systems.frontier_consult.route.team_dispatch", _capture):
        await spawn_generate_lane_review_child(
            request_id="req-c1",
            ctx=ctx,
            parent_thread_id="thread-leg-c-worker",
            reviewer=ReviewerSelection(
                model="cdp/opus-5",
                identity=consultant_identity("cdp/opus-5", None),
                contract="code-review",
                effort="high",
            ),
        )

    assert len(captured) == 1
    body = captured[0]
    assert isinstance(body, TeamDispatchGenerateBody)
    assert body.prompt_turn_number is None
    assert body.prompt_bind_mode == "explicit_external"
    assert body.prompt_sha256 is None
    assert "<review_mandate>" in (body.prompt or "")
    token, bind, sha, _chars, fenced = _extract_fenced_instruction(body.prompt or "")
    assert bind == "explicit_external"
    assert fenced == _INLINE_TEXT
    assert sha == hashlib.sha256(_INLINE_TEXT.encode()).hexdigest()
    assert sha == ctx.frozen_instruction_sha256
    assert _closer_line_count(body.prompt or "", token) == 1
    assert (body.prompt or "").count(_INLINE_TEXT) == 1
    assert _instruction_not_in_composer_draft(body.prompt or "", _INLINE_TEXT)
    enforce_review_prompt_gate(
        contract="code-review",
        prompt_text=body.prompt or "",
        dispatch_thread_id="thread:leg-c-coord",
    )


@pytest.mark.asyncio
async def test_leg_c_packet_parent_review_child_carries_admit_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from systems.frontier_consult.cursor_sdk_admit_loop import (
        classify_admit_pointer_loop,
    )
    from systems.frontier_consult.dispatch_thread_context import (
        reject_unresolved_placeholders,
    )
    from systems.frontier_consult.review_child_spawn_hook import (
        spawn_generate_lane_review_child,
    )
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    packet_path = _leg_c_write_packet(tmp_path, _PACKET_TEXT)
    exec_id = "exec-leg-c-c2"
    ctx = await _leg_c_admit_parent(
        monkeypatch,
        execution_id=exec_id,
        message_text=None,
        packet_path=packet_path,
        tmp_path=tmp_path,
    )
    captured: list[object] = []

    async def _capture(body: object, _resp: object) -> dict[str, str]:
        captured.append(body)
        return {"execution_id": "child-leg-c"}

    with patch("systems.frontier_consult.route.team_dispatch", _capture):
        await spawn_generate_lane_review_child(
            request_id="req-c2",
            ctx=ctx,
            parent_thread_id="thread-leg-c-worker",
            reviewer=ReviewerSelection(
                model="cursor/composer-2.5",
                identity=consultant_identity("cursor/composer-2.5", None),
                contract="check-review",
                effort=None,
            ),
        )

    assert len(captured) == 1
    body = captured[0]
    assert isinstance(body, TeamDispatchGenerateBody)
    assert body.seat == "cursor-sdk"
    assert body.lane == "B"
    assert body.read_only is True
    assert body.prompt_turn_number is None
    assert body.prompt_bind_mode == "explicit_external"
    assert (body.prompt or "").startswith(_REVIEWER_EMPTY_SCOPE_PREFIX)
    _token, _bind, sha, _chars, fenced = _extract_fenced_instruction(body.prompt or "")
    assert fenced == _PACKET_TEXT
    assert sha == ctx.frozen_instruction_sha256
    reject_unresolved_placeholders(
        request_id="req-c2",
        text=body.prompt or "",
        field="prompt",
    )
    loop = classify_admit_pointer_loop(
        admit_target_thread=body.dispatch_thread_id or "",
        prompt_source_thread=body.dispatch_thread_id or "",
        prompt_bind_mode=body.prompt_bind_mode,
        prompt_turn_number=body.prompt_turn_number,
        has_explicit_prompt_source=True,
    )
    assert loop.loop_closure is False


@pytest.mark.asyncio
async def test_leg_c_packet_edit_after_admit_does_not_change_review(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from systems.frontier_consult.review_child_spawn_hook import (
        spawn_generate_lane_review_child,
    )
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    packet_path = _leg_c_write_packet(tmp_path, _PACKET_TEXT)
    packet_file = tmp_path / "leg-c-ac" / packet_path.rsplit("/", 1)[-1]
    exec_id = "exec-leg-c-c3"
    ctx = await _leg_c_admit_parent(
        monkeypatch,
        execution_id=exec_id,
        message_text=None,
        packet_path=packet_path,
        tmp_path=tmp_path,
    )
    packet_file.write_text(
        _PACKET_TEXT.replace("marker: version-A", "marker: version-B"),
        encoding="utf-8",
    )
    captured: list[object] = []

    async def _capture(body: object, _resp: object) -> dict[str, str]:
        captured.append(body)
        return {"execution_id": "child-leg-c"}

    with patch("systems.frontier_consult.route.team_dispatch", _capture):
        await spawn_generate_lane_review_child(
            request_id="req-c3",
            ctx=ctx,
            parent_thread_id="thread-leg-c-worker",
            reviewer=ReviewerSelection(
                model="cursor/composer-2.5",
                identity=consultant_identity("cursor/composer-2.5", None),
                contract="check-review",
                effort=None,
            ),
        )

    assert len(captured) == 1
    body = captured[0]
    assert isinstance(body, TeamDispatchGenerateBody)
    assert "marker: version-A" in (body.prompt or "")
    assert "marker: version-B" not in (body.prompt or "")
    _token, _bind, sha, _chars, _fenced = _extract_fenced_instruction(body.prompt or "")
    assert sha == hashlib.sha256(_PACKET_TEXT.encode()).hexdigest()


@pytest.mark.asyncio
async def test_leg_c_frozen_turn_instruction_inside_boundary() -> None:
    from systems.frontier_consult.review_child_spawn_hook import (
        spawn_generate_lane_review_child,
    )
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    turn_body = "Turn twelve body line A.\nTurn twelve body line B.\n"
    write_admission_context(
        execution_id="exec-leg-c-c5",
        auto_review_child=True,
        op="generate",
        role="cursor-sdk",
        resolved_model="openai/gpt-5.5",
        parent_dispatch_thread_id="thread:coord",
        dispatch_thread_id="thread:coord",
        prompt_turn_number=12,
        prompt_bind_mode="frozen_turn",
        caller_agent="cursor-sdk-lane",
        work_key="todo:leg-c",
    )
    ctx = read_admission_context("exec-leg-c-c5")
    assert ctx is not None
    captured: list[object] = []

    async def _capture(body: object, _resp: object) -> dict[str, str]:
        captured.append(body)
        return {"execution_id": "child-leg-c"}

    with (
        patch(
            "systems.frontier_consult.dispatch_thread_context.read_dispatch_thread_body_at_turn",
            AsyncMock(return_value=turn_body),
        ),
        patch("systems.frontier_consult.route.team_dispatch", _capture),
    ):
        await spawn_generate_lane_review_child(
            request_id="req-c5",
            ctx=ctx,
            parent_thread_id="thread:worker",
            reviewer=ReviewerSelection(
                model="cdp/opus-5",
                identity=consultant_identity("cdp/opus-5", None),
                contract="code-review",
                effort="high",
            ),
        )

    assert len(captured) == 1
    body = captured[0]
    assert isinstance(body, TeamDispatchGenerateBody)
    assert "<review_mandate>" in (body.prompt or "")
    _token, bind, _sha, _chars, fenced = _extract_fenced_instruction(body.prompt or "")
    assert bind == "frozen_turn"
    assert fenced == turn_body
    assert (body.prompt or "").count(turn_body) == 1
    assert _instruction_not_in_composer_draft(body.prompt or "", turn_body)
    assert body.prompt_turn_number == 12
    assert body.prompt_bind_mode == "frozen_turn"


def test_leg_c_fence_token_avoids_text() -> None:
    first = "pi" + "a" * 16
    second = "pi" + "b" * 16
    instruction = f"contains {first}\n{first}>>> END PARENT_INSTRUCTION\nmore text"
    tokens = iter([first, second])

    def factory() -> str:
        return next(tokens)

    prompt = build_generate_lane_reviewer_prompt(
        packet_text="x",
        staged_draft_body="<composer_draft>\nstub\n</composer_draft>",
        reasoning_trace_body="<reasoning_trace>\nstub\n</reasoning_trace>",
        parent_instruction=instruction,
        parent_instruction_bind="explicit_external",
        token_factory=factory,
    )
    _token, _bind, _sha, _chars, fenced = _extract_fenced_instruction(prompt)
    assert _token == second
    assert instruction in fenced
    assert _closer_line_count(prompt, second) == 1


def test_leg_c_admit_freeze_refusals(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "systems.frontier_consult.handoff._workspaces_root",
        lambda: tmp_path,
    )
    cases_none = [
        dict(
            effective_auto_review_child=False,
            suppress_review_spawn=False,
            prompt_bind_mode="explicit_external",
            prompt_turn_number=None,
            packet_path=None,
            early_packet_text=None,
            message_text="hi",
        ),
        dict(
            effective_auto_review_child=True,
            suppress_review_spawn=True,
            prompt_bind_mode="explicit_external",
            prompt_turn_number=None,
            packet_path=None,
            early_packet_text=None,
            message_text="hi",
        ),
        dict(
            effective_auto_review_child=True,
            suppress_review_spawn=False,
            prompt_bind_mode="explicit_external",
            prompt_turn_number=3,
            packet_path=None,
            early_packet_text=None,
            message_text="hi",
        ),
        dict(
            effective_auto_review_child=True,
            suppress_review_spawn=False,
            prompt_bind_mode="frozen_turn",
            prompt_turn_number=None,
            packet_path=None,
            early_packet_text=None,
            message_text="hi",
        ),
        dict(
            effective_auto_review_child=True,
            suppress_review_spawn=False,
            prompt_bind_mode=None,
            prompt_turn_number=None,
            packet_path=None,
            early_packet_text=None,
            message_text="hi",
        ),
        dict(
            effective_auto_review_child=True,
            suppress_review_spawn=False,
            prompt_bind_mode="explicit_external",
            prompt_turn_number=None,
            packet_path="workspaces://missing/none.md",
            early_packet_text=None,
            message_text=None,
        ),
        dict(
            effective_auto_review_child=True,
            suppress_review_spawn=False,
            prompt_bind_mode="explicit_inline",
            prompt_turn_number=None,
            packet_path=None,
            early_packet_text=None,
            message_text="   ",
        ),
        dict(
            effective_auto_review_child=True,
            suppress_review_spawn=False,
            prompt_bind_mode="explicit_external",
            prompt_turn_number=None,
            packet_path=None,
            early_packet_text=None,
            message_text="x" * (FROZEN_INSTRUCTION_MAX_BYTES + 1),
        ),
    ]
    for kwargs in cases_none:
        assert resolve_frozen_instruction_at_admit(**kwargs) is None

    assert (
        resolve_frozen_instruction_at_admit(
            effective_auto_review_child=True,
            suppress_review_spawn=False,
            prompt_bind_mode="explicit_inline",
            prompt_turn_number=None,
            packet_path=None,
            early_packet_text=None,
            message_text="inline-eligible",
        )
        == "inline-eligible"
    )
    assert (
        resolve_frozen_instruction_at_admit(
            effective_auto_review_child=True,
            suppress_review_spawn=False,
            prompt_bind_mode="explicit_external",
            prompt_turn_number=None,
            packet_path="workspaces://leg-c-ac/p.md",
            early_packet_text="early-bytes",
            message_text=None,
        )
        == "early-bytes"
    )


@pytest.mark.asyncio
async def test_leg_c_gate_refused_prompt_abandons_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from systems.frontier_consult.review_child_spawn_hook import (
        reconcile_pending_spawns,
    )

    exec_id = "exec-leg-c-c10"
    await _leg_c_admit_parent(
        monkeypatch,
        execution_id=exec_id,
        message_text=_GATE_REFUSED_TEXT,
        packet_path=None,
    )
    try_claim_spawn_pending(
        parent_execution_id=exec_id,
        parent_dispatch_thread_id="thread:leg-c-coord",
        parent_thread_id="thread:worker",
        reviewer_model="cdp/opus-5",
    )
    captured: list[object] = []
    published: list[str] = []
    with (
        patch(
            "systems.frontier_consult.route.team_dispatch",
            AsyncMock(
                side_effect=lambda b, _r: (
                    captured.append(b) or {"execution_id": "child"}
                )
            ),
        ),
        patch(
            "systems.frontier_consult.review_child_spawn_hook.durable_catch_up_terminal",
            side_effect=_terminal_stub,
        ),
        patch(
            "systems.frontier_consult.review_child_spawn_hook.publish_frontier_event",
            side_effect=lambda ev: published.append(ev.signal),
        ),
    ):
        await reconcile_pending_spawns()
        await reconcile_pending_spawns()

    state = read_spawn_state(exec_id)
    assert state is not None
    assert state.state == "final"
    assert state.review_child_execution_id is None
    meta = json.loads(state.attempt_metadata)
    assert meta["abandon_reason"] == "review_prompt_gate_refused"
    assert captured == []
    assert published.count("frontier.review_child.prompt_bind") == 1


_CALLER_INVENTORY_PACKET = """
<scope>
Files expected: - `libs/claude_bundles/holder_strings.py`
</scope>
<task_guidance>
Change the holder string formatter.
</task_guidance>
"""


@pytest.mark.asyncio
async def test_caller_inventory_source_prompt_carries_block() -> None:
    with patch(
        "systems.frontier_consult.dispatch_thread_context.read_dispatch_thread_body_at_turn",
        AsyncMock(return_value=_CALLER_INVENTORY_PACKET),
    ):
        build = await _build_generate_lane_review_prompt(
            request_id="req-ci",
            parent_dispatch_thread_id="thread:ci",
            prompt_turn_number=3,
            prompt_bind_mode="frozen_turn",
        )
    assert build is not None
    prompt = build.prompt
    assert '<caller_inventory data_only="true" status="complete"' in prompt
    site_line = (
        "libs/cdp_ask/work_projection.py:124 imports format_cowork_cse_holder, "
        "holder_id_from_chat_url from claude_bundles.holder_strings "
        "[outside files_expected]"
    )
    assert site_line in prompt
    assert "Caller inventory: machine-generated from the repository." in prompt
    token, _bind, _sha, _chars, _fenced = _extract_fenced_instruction(prompt)
    assert prompt.index(f"{token}>>> END PARENT_INSTRUCTION") < prompt.rindex(
        "<caller_inventory"
    )
    assert prompt.rindex("<caller_inventory") < prompt.index(
        FOOTER_BY_SURFACE["source"]
    )


@pytest.mark.asyncio
async def test_caller_inventory_sidecar_no_block() -> None:
    with patch(
        "systems.frontier_consult.dispatch_thread_context.read_dispatch_thread_body_at_turn",
        AsyncMock(return_value=_SIDECAR_PACKET),
    ):
        build = await _build_generate_lane_review_prompt(
            request_id="req-ci-sidecar",
            parent_dispatch_thread_id="thread:ci-sidecar",
            prompt_turn_number=2,
            prompt_bind_mode="frozen_turn",
        )
    assert build is not None
    assert "<caller_inventory" not in build.prompt


@pytest.mark.asyncio
async def test_caller_inventory_not_computed_without_lib_path() -> None:
    with patch(
        "systems.frontier_consult.dispatch_thread_context.read_dispatch_thread_body_at_turn",
        AsyncMock(return_value=_SOURCE_PACKET),
    ):
        build = await _build_generate_lane_review_prompt(
            request_id="req-ci-nc",
            parent_dispatch_thread_id="thread:ci-nc",
            prompt_turn_number=2,
            prompt_bind_mode="frozen_turn",
        )
    assert build is not None
    assert (
        'status="not_computed" reason="no_lib_paths_in_files_expected"' in build.prompt
    )


@pytest.mark.asyncio
async def test_caller_inventory_frontmatter_list() -> None:
    packet = (
        "---\nfiles_expected:\n  - libs/claude_bundles/holder_strings.py\n---\n"
        "<scope>\nNo files here.\n</scope>\n"
        "<task_guidance>\nChange holder.\n</task_guidance>\n"
    )
    with patch(
        "systems.frontier_consult.dispatch_thread_context.read_dispatch_thread_body_at_turn",
        AsyncMock(return_value=packet),
    ):
        build = await _build_generate_lane_review_prompt(
            request_id="req-ci-fm",
            parent_dispatch_thread_id="thread:ci-fm",
            prompt_turn_number=2,
            prompt_bind_mode="frozen_turn",
        )
    assert build is not None
    assert "libs/cdp_ask/work_projection.py:124" in build.prompt


@pytest.mark.asyncio
async def test_caller_inventory_bullet_after_declaration() -> None:
    packet = (
        "<scope>\nFiles expected:\n"
        "- `libs/claude_bundles/holder_strings.py`\n"
        "</scope>\n<task_guidance>\nChange holder.\n</task_guidance>\n"
    )
    with patch(
        "systems.frontier_consult.dispatch_thread_context.read_dispatch_thread_body_at_turn",
        AsyncMock(return_value=packet),
    ):
        build = await _build_generate_lane_review_prompt(
            request_id="req-ci-bullet",
            parent_dispatch_thread_id="thread:ci-bullet",
            prompt_turn_number=2,
            prompt_bind_mode="frozen_turn",
        )
    assert build is not None
    assert "libs/cdp_ask/work_projection.py:124" in build.prompt


@pytest.mark.asyncio
async def test_caller_inventory_mention_does_not_widen() -> None:
    packet = _CALLER_INVENTORY_PACKET.replace(
        "</task_guidance>",
        "do not edit `libs/cdp_ask/work_projection.py`\n</task_guidance>",
    )
    with patch(
        "systems.frontier_consult.dispatch_thread_context.read_dispatch_thread_body_at_turn",
        AsyncMock(return_value=packet),
    ):
        build = await _build_generate_lane_review_prompt(
            request_id="req-ci-mention",
            parent_dispatch_thread_id="thread:ci-mention",
            prompt_turn_number=2,
            prompt_bind_mode="frozen_turn",
        )
    assert build is not None
    assert 'modules="1"' in build.prompt
    assert "libs/cdp_ask/work_projection.py:124" in build.prompt


@pytest.mark.asyncio
async def test_caller_inventory_passes_dispatch_checks() -> None:
    from claude_bundles.cdp_review_prompt_gate import enforce_review_prompt_gate
    from systems.frontier_consult.dispatch_thread_context import (
        reject_unresolved_placeholders,
    )

    with patch(
        "systems.frontier_consult.dispatch_thread_context.read_dispatch_thread_body_at_turn",
        AsyncMock(return_value=_CALLER_INVENTORY_PACKET),
    ):
        build = await _build_generate_lane_review_prompt(
            request_id="req-ci-gate",
            parent_dispatch_thread_id="thread:ci",
            prompt_turn_number=3,
            prompt_bind_mode="frozen_turn",
        )
    assert build is not None
    prompt = build.prompt
    enforce_review_prompt_gate(
        contract="code-review",
        prompt_text=prompt,
        dispatch_thread_id="thread:ci",
    )
    reject_unresolved_placeholders(
        request_id="req-ci",
        text=_REVIEWER_EMPTY_SCOPE_PREFIX + prompt,
        field="prompt",
    )


@pytest.mark.asyncio
async def test_caller_inventory_absent_without_parent_instruction() -> None:
    build = await _build_generate_lane_review_prompt(
        request_id="req-ci-absent",
        parent_dispatch_thread_id="thread:ci-absent",
        prompt_turn_number=None,
        prompt_bind_mode="explicit_inline",
        frozen_instruction_text=None,
    )
    assert build is not None
    assert "<caller_inventory" not in build.prompt
