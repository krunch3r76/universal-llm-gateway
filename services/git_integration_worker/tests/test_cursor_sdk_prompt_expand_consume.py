"""GIW prompt-expand consume router hook tests."""

from __future__ import annotations

import pytest
from prompt_expand_consume.router import ConsumeBranch
from systems.frontier_consult.prompt_expand_prelude import ExpandRun

from services.git_integration_worker.cursor_sdk_prompt_expand import (
    maybe_expand_giw_prompt,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "11767",
        "model": "cursor/composer-2.5",
        "dispatch_id": "consume-test",
        "execution_id": "exec-consume-test",
        "message": "Expand in the window for enrolled root wiring.",
        "handoff_contract": "pure-mechanical",
        "operator_contract": "implement",
        "continuity_root_thread_id": "10479",
        "parent_dispatch_thread_id": "10479",
        "caller_agent": "web-anthropic",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_maybe_expand_giw_prompt_window_transcript_id_in_seat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attended via caller_transcript_id without summon_mode in message body."""
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.run_prompt_expand",
        lambda task, options: ExpandRun(
            ok=True,
            prompt="---\npipeline: prompt-expand\n---\n\nTASK'",
            execution_id="exp-giw-tid",
        ),
    )
    result = await maybe_expand_giw_prompt(
        _req(
            message="Expand in the window for enrolled root wiring.",
            caller_transcript_id="550e8400-e29b-41d4-a716-446655440000",
        ),
        "Expand in the window for enrolled root wiring.",
        handoff_contract="pure-mechanical",
        resolved_model="cursor/composer-2.5",
    )
    assert result.skip_dispatch is True
    assert result.decision is not None
    assert result.decision.branch is ConsumeBranch.IN_SEAT
    assert result.activation_envelope is not None
    assert result.activation_envelope.get("X-ULG-Transcript-Id") == (
        "550e8400-e29b-41d4-a716-446655440000"
    )


@pytest.mark.offline
@pytest.mark.asyncio
async def test_maybe_expand_giw_prompt_in_seat_short_circuit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.run_prompt_expand",
        lambda task, options: ExpandRun(
            ok=True,
            prompt="---\npipeline: prompt-expand\n---\n\nTASK'",
            execution_id="exp-giw",
        ),
    )
    result = await maybe_expand_giw_prompt(
        _req(),
        _req().message or "",
        handoff_contract="pure-mechanical",
        resolved_model="cursor/composer-2.5",
        attended=True,
    )
    assert result.skip_dispatch is True
    assert result.decision is not None
    assert result.decision.branch is ConsumeBranch.IN_SEAT
    assert result.activation_envelope is not None


@pytest.mark.offline
@pytest.mark.asyncio
async def test_maybe_expand_giw_prompt_sdk_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.run_prompt_expand",
        lambda task, options: ExpandRun(
            ok=True,
            prompt="---\npipeline: prompt-expand\n---\n\nTASK'",
            execution_id="exp-giw-bg",
        ),
    )
    result = await maybe_expand_giw_prompt(
        _req(message="plain commission without window verb"),
        "plain commission without window verb",
        handoff_contract="pure-mechanical",
        resolved_model="cursor/composer-2.5",
        attended=False,
    )
    assert result.skip_dispatch is False
    assert result.decision is not None
    assert result.decision.branch is ConsumeBranch.SDK_BACKGROUND


@pytest.mark.offline
def test_result_from_decision_in_seat_skips_dispatch() -> None:
    from prompt_expand_consume.router import ConsumeDecision

    from services.git_integration_worker.cursor_sdk_prompt_expand import (
        _result_from_decision,
    )

    decision = ConsumeDecision(
        branch=ConsumeBranch.IN_SEAT,
        reason="operator_verb:window",
        activation_header={"X-ULG-Consume-Branch": "in_seat"},
    )
    result = _result_from_decision("TASK'", decision, dispatch_id="d1")
    assert result.skip_dispatch is True
    assert result.activation_envelope is not None


@pytest.mark.offline
@pytest.mark.asyncio
async def test_maybe_expand_giw_conductor_advisory_skips_sdk_but_delivers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.run_prompt_expand",
        lambda task, options: ExpandRun(
            ok=True,
            prompt="---\npipeline: prompt-expand\n---\n\nTASK'",
            execution_id="exp-giw-conductor",
        ),
    )
    result = await maybe_expand_giw_prompt(
        _req(message="plain commission without hints"),
        "plain commission without hints",
        handoff_contract="pure-mechanical",
        resolved_model="cursor/composer-2.5",
        attended=True,
        durable_session=True,
    )
    assert result.skip_dispatch is True
    assert result.consume_advisory is True
    assert result.decision is not None
    assert result.decision.branch is ConsumeBranch.CONDUCTOR_RECOMMEND
    assert "TASK'" in result.prompt


@pytest.mark.offline
def test_result_from_decision_sdk_background_continues_dispatch() -> None:
    from prompt_expand_consume.router import ConsumeDecision

    from services.git_integration_worker.cursor_sdk_prompt_expand import (
        _result_from_decision,
    )

    decision = ConsumeDecision(
        branch=ConsumeBranch.SDK_BACKGROUND,
        reason="default",
    )
    result = _result_from_decision("TASK'", decision, dispatch_id="d2")
    assert result.skip_dispatch is False
