"""Caller-door prelude: skip wakes, restage CDP prompts, rewrite SDK packets."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from implement_admission.prompt_expand_admit import PIPELINE_ID
from prompt_expand_consume.router import ConsumeBranch

from systems.frontier_consult.cursor_sdk_prepared_handle import (
    PreparedCursorSdkHandle,
)
from systems.frontier_consult.prompt_expand_prelude import (
    ExpandRun,
    apply_expand_to_handle,
    consume_admit_fields,
    expand_consume_admit_path,
    maybe_expand_cdp_prompt,
    sdk_should_expand,
)


def _handle(**overrides: object) -> PreparedCursorSdkHandle:
    base = PreparedCursorSdkHandle(
        request_id="req",
        execution_id="exec-1",
        dispatch_id="disp-1",
        thread_id="11690",
        resolved_model="cursor/composer-2.5",
        role="cursor-sdk",
        family="cursor",
        platform="sdk",
        to_agent="cursor-sdk",
        handoff_contract="implement",
        packet_path=None,
        message="# Next-Seat Dispatch Packet\n\nFix the catalog skip.",
        caller_agent="web-anthropic",
        read_only=False,
        aligned_knobs=None,
        prompt_preamble=None,
        thread_subject="hop",
        pointer_body="",
        effective_bus_lifecycle="ephemeral",
        parent_dispatch_thread_id="10479",
        dispatch_thread_id="11690",
        density_triage=None,
        review_opt_out_reason_code=None,
        auto_review_child=False,
        auto_review_defaulted=False,
        claimed_via_atomic=False,
        admitted=True,
        alignment_warnings=(),
        knob_resolution=(),
    )
    return replace(base, **overrides) if overrides else base


@pytest.mark.offline
def test_sdk_should_not_expand_10479_implement() -> None:
    assert sdk_should_expand(_handle()) is False


@pytest.mark.offline
def test_sdk_should_not_expand_10479_none() -> None:
    assert sdk_should_expand(_handle(handoff_contract="none")) is False


@pytest.mark.offline
def test_sdk_should_expand_10479_sketch() -> None:
    assert sdk_should_expand(_handle(handoff_contract="sketch")) is True


@pytest.mark.offline
def test_sdk_should_not_expand_wake_message() -> None:
    handle = _handle(message="WAKE — liaison headless successor, house agent-bus:10479")
    assert sdk_should_expand(handle) is False


@pytest.mark.offline
def test_maybe_expand_cdp_restages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "in.md"
    src.write_text("TASK body for a 10479 consult", encoding="utf-8")
    staged_uri = "cortex://notes/system/ephemeral/out.md"

    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.read_prompt_uri",
        lambda uri: src.read_text(encoding="utf-8"),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.run_prompt_expand",
        lambda task, options: ExpandRun(
            ok=True,
            prompt=f"---\npipeline: {PIPELINE_ID}\n---\n" + task,
            execution_id="exp-1",
        ),
    )

    class _Staged:
        prompt_uri = staged_uri

    monkeypatch.setattr(
        "claude_bundles.cdp_model_endpoint_staging.stage_prompt_uri",
        lambda **kwargs: _Staged(),
    )
    out = maybe_expand_cdp_prompt(
        prompt_uri="cortex://notes/system/ephemeral/in.md",
        execution_id="gen-1",
        thread_id="10479",
        parent_thread="10479",
        caller_agent="web-anthropic",
        contract="consult",
        model_id="cdp/opus-5",
    )
    assert out == staged_uri


@pytest.mark.offline
def test_apply_expand_rewrites_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.run_prompt_expand",
        lambda task, options: ExpandRun(ok=True, prompt="TASK'", execution_id="exp-2"),
    )
    out = apply_expand_to_handle(_handle())
    assert "pipeline: prompt-expand" in (out.message or "")
    assert out.consume_branch == ConsumeBranch.SDK_BACKGROUND.value
    assert out.execution_id == "exec-1"


@pytest.mark.offline
def test_apply_expand_window_verb_routes_in_seat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.run_prompt_expand",
        lambda task, options: ExpandRun(
            ok=True,
            prompt="---\npipeline: prompt-expand\n---\n\nTASK'",
            execution_id="exp-window",
        ),
    )
    handle = _handle(
        message="Expand this in the window for 10479.\nsummon_mode: attended",
    )
    out = apply_expand_to_handle(handle)
    assert out.consume_branch == ConsumeBranch.IN_SEAT.value
    fields = consume_admit_fields(out)
    assert fields["consume_branch"] == "in_seat"
    assert fields["activation"]["summoning_thread_id"] == "11690"


@pytest.mark.offline
def test_apply_expand_window_verb_transcript_id_routes_in_seat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production AC: attended via transcript_id, not summon_mode in body."""
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.run_prompt_expand",
        lambda task, options: ExpandRun(
            ok=True,
            prompt="---\npipeline: prompt-expand\n---\n\nTASK'",
            execution_id="exp-window-tid",
        ),
    )
    handle = _handle(message="Expand this in the window for 10479.")
    out = apply_expand_to_handle(
        handle,
        transcript_id="550e8400-e29b-41d4-a716-446655440000",
    )
    assert out.consume_branch == ConsumeBranch.IN_SEAT.value
    fields = consume_admit_fields(out)
    assert fields["consume_branch"] == "in_seat"
    assert "pipeline: prompt-expand" in fields["task_prime"]
    assert fields["activation"]["transcript_id"] == (
        "550e8400-e29b-41d4-a716-446655440000"
    )
    assert fields["activation"]["summoning_thread_id"] == "11690"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_expand_consume_admit_delivers_in_seat_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.run_prompt_expand",
        lambda task, options: ExpandRun(
            ok=True,
            prompt="fire_hint: in_seat\n\nTASK'",
            execution_id="exp-in-seat",
        ),
    )
    dispatched: list[PreparedCursorSdkHandle] = []

    async def _dispatch(handle: PreparedCursorSdkHandle) -> None:
        dispatched.append(handle)

    admit = await expand_consume_admit_path(
        _handle(message="plain commission", handoff_contract="sketch"),
        _dispatch,
    )
    assert admit.deliver_handle is not None
    assert admit.deliver_handle.consume_branch == ConsumeBranch.IN_SEAT.value
    fields = consume_admit_fields(admit.deliver_handle)
    assert fields["consume_branch"] == "in_seat"
    assert "fire_hint: in_seat" in fields["task_prime"]
    assert "pipeline: prompt-expand" in fields["task_prime"]
    assert fields["activation"]["summoning_thread_id"] == "11690"
    assert dispatched == []


@pytest.mark.offline
@pytest.mark.asyncio
async def test_expand_consume_admit_dispatches_sdk_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.run_prompt_expand",
        lambda task, options: ExpandRun(
            ok=True,
            prompt="---\npipeline: prompt-expand\n---\n\nTASK'",
            execution_id="exp-sdk",
        ),
    )
    dispatched: list[PreparedCursorSdkHandle] = []

    async def _dispatch(handle: PreparedCursorSdkHandle) -> None:
        dispatched.append(handle)

    admit = await expand_consume_admit_path(
        _handle(handoff_contract="sketch"), _dispatch
    )
    assert admit.scheduled_background is True
    assert admit.deliver_handle is None
    await asyncio.sleep(0.05)
    assert len(dispatched) == 1
    assert dispatched[0].consume_branch == ConsumeBranch.SDK_BACKGROUND.value


@pytest.mark.offline
@pytest.mark.asyncio
async def test_expand_consume_admit_returns_in_seat_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.run_prompt_expand",
        lambda task, options: ExpandRun(
            ok=True,
            prompt="fire_hint: in_seat\n\nTASK'",
            execution_id="exp-sched-in-seat",
        ),
    )
    admit = await expand_consume_admit_path(
        _handle(message="plain commission", handoff_contract="sketch"),
        pytest.fail,
    )
    assert admit.scheduled_background is False
    assert admit.deliver_handle is not None
    assert admit.deliver_handle.consume_branch == ConsumeBranch.IN_SEAT.value
    fields = consume_admit_fields(admit.deliver_handle)
    assert "fire_hint: in_seat" in fields["task_prime"]


@pytest.mark.offline
def test_apply_expand_persistent_lifecycle_conductor_recommend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production AC: attended + durable via transcript_id and bus_lifecycle only."""
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.run_prompt_expand",
        lambda task, options: ExpandRun(
            ok=True,
            prompt="---\npipeline: prompt-expand\n---\n\nTASK'",
            execution_id="exp-conductor-prod",
        ),
    )
    handle = _handle(
        message="plain commission without hints",
        effective_bus_lifecycle="persistent",
    )
    out = apply_expand_to_handle(
        handle,
        transcript_id="550e8400-e29b-41d4-a716-446655440000",
    )
    assert out.consume_branch == ConsumeBranch.CONDUCTOR_RECOMMEND.value
    fields = consume_admit_fields(out)
    assert fields.get("consume_advisory") is True
    assert fields["consume_branch"] == "conductor_recommend"


@pytest.mark.offline
def test_apply_expand_packet_path_emits_task_prime_on_admit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.workspaces_root",
        lambda: tmp_path,
    )
    packet = tmp_path / "packets" / "commission.md"
    packet.parent.mkdir(parents=True)
    packet.write_text(
        "Expand in the window for 10479.\n\n# packet body",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.run_prompt_expand",
        lambda task, options: ExpandRun(
            ok=True,
            prompt="---\npipeline: prompt-expand\n---\n\nTASK'",
            execution_id="exp-packet",
        ),
    )
    handle = _handle(
        packet_path="packets/commission.md",
        message=None,
    )
    out = apply_expand_to_handle(
        handle,
        transcript_id="550e8400-e29b-41d4-a716-446655440000",
    )
    assert out.packet_path is not None
    assert out.consume_branch == ConsumeBranch.IN_SEAT.value
    fields = consume_admit_fields(out)
    assert "pipeline: prompt-expand" in fields["task_prime"]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_dispatch_prepared_cursor_sdk_delivers_consume_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.frontier_consult.cursor_sdk_generate import (
        dispatch_prepared_cursor_sdk,
    )

    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.run_prompt_expand",
        lambda task, options: ExpandRun(
            ok=True,
            prompt="fire_hint: in_seat\n\nTASK'",
            execution_id="exp-dispatch",
        ),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_worker",
        pytest.fail,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_worker_message",
        pytest.fail,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.emit_poll_hint_from_handoff",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.emit_sdk_worker_outcome",
        lambda **_kwargs: None,
    )

    result = await dispatch_prepared_cursor_sdk(
        _handle(message="plain commission", handoff_contract="sketch")
    )
    assert result["prompt_expand"] == "complete"
    assert result["consume_branch"] == "in_seat"
    assert "fire_hint: in_seat" in result["task_prime"]
    assert "pipeline: prompt-expand" in result["task_prime"]
    assert result["activation"]["summoning_thread_id"] == "11690"


@pytest.mark.offline
def test_expand_transport_failure_skips_router_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.run_prompt_expand",
        lambda task, options: ExpandRun(ok=False, prompt=task, error="transport"),
    )
    out = apply_expand_to_handle(_handle())
    assert out.consume_branch is None
    assert out.message == _handle().message
