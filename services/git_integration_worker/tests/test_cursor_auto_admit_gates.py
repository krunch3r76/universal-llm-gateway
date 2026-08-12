"""Unit tests for cursor-auto pre-nest empty-scope admit gates (friction-26765)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.git_integration_worker.cursor_auto.admit_gates import (
    AdmitGateResult,
    blocking_admit_gate,
)
from services.git_integration_worker.cursor_auto.directive import (
    effective_contract,
    has_actionable_scope,
    has_vision_field,
)
from services.git_integration_worker.cursor_auto.handler import process_job
from services.git_integration_worker.cursor_auto.queue import AutoJob


def _bus_client() -> AsyncMock:
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body="{}"))
    return client


@pytest.fixture(autouse=True)
def _capture_events(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, dict[str, object]]]:
    emitted: list[tuple[str, dict[str, object]]] = []

    def _capture(signal: str, **payload: object) -> None:
        emitted.append((signal, dict(payload)))

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_events.record",
        _capture,
    )
    return emitted


def test_has_actionable_scope_tokens() -> None:
    assert not has_actionable_scope("TYPE: DIRECTIVE\ndensity: dense\n")
    assert has_actionable_scope("TYPE: DIRECTIVE\n## Scope\nfoo")
    assert has_actionable_scope(
        "TYPE: DIRECTIVE\nscope: services/git_integration_worker/\n"
        "out-of-scope: docs/\n"
    )
    assert has_actionable_scope("TYPE: DIRECTIVE\n<scope>foo</scope>")
    assert has_actionable_scope("TYPE: DIRECTIVE\nsource_ref: todo:x")
    assert has_actionable_scope("TYPE: DIRECTIVE\ntodo:friction-26765")
    assert has_actionable_scope("TYPE: DIRECTIVE\nsee packet:foo.md")
    assert has_actionable_scope("TYPE: DIRECTIVE\nfiles_expected: a.py")
    assert has_actionable_scope(
        'TYPE: DIRECTIVE\nProse mentions todo:friction-1 in quotes.'
    )


@pytest.mark.asyncio
async def test_blocking_admit_gate_empty_dense_directive_blocks_first() -> None:
    fetch = AsyncMock(return_value=[])
    job = AutoJob(
        job_id="j-empty",
        thread_id="5899",
        turn_number=1,
        subject="empty",
        body="TYPE: DIRECTIVE\ndensity: dense\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
            fetch,
        )
        gate_out = await blocking_admit_gate(
            job,
            client=_bus_client(),
            queue=MagicMock(),
        )
        blocked = gate_out.blocked
    assert blocked is not None
    assert blocked["terminal_status"] == "status:blocked"
    assert "empty_directive_scope" in blocked["summary"]
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_blocking_admit_gate_contract_override_waives(
    _capture_events: list[tuple[str, dict[str, object]]],
) -> None:
    job = AutoJob(
        job_id="j-waive",
        thread_id="5899",
        turn_number=1,
        subject="waive",
        body="TYPE: DIRECTIVE\ndensity: dense\ncontract: implement\nvision: mechanical — scope override waive test\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_status",
            AsyncMock(return_value="active"),
        )
        mp.setattr(
            "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
            AsyncMock(return_value=[]),
        )
        result = await blocking_admit_gate(
            job,
            client=_bus_client(),
            queue=MagicMock(),
        )
    assert result.blocked is None
    assert any(
        sig == "frontier.sdk.auto.empty_directive_scope_waived"
        for sig, _ in _capture_events
    )


def test_process_job_empty_directive_never_nests(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    submit = AsyncMock()
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.submit_nested_dispatch",
        submit,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        lambda: {"active": 0, "queued": 0, "limit": 1},
    )
    job = AutoJob(
        job_id="j-block",
        thread_id="5899",
        turn_number=1,
        subject="empty",
        body="TYPE: DIRECTIVE\ndensity: dense\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="answer",
    )
    result = asyncio.run(process_job(job, bus=bus))
    assert result.blocked["terminal_status"] == "status:blocked"
    assert "empty_directive_scope" in result.blocked["summary"]
    submit.assert_not_awaited()
    assert job.contract == "implement"


def test_process_job_non_directive_contract_still_runs_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = AsyncMock(
        return_value=AdmitGateResult(
            blocked={"terminal_status": "status:blocked"},
        )
    )
    bus = AsyncMock()
    submit = AsyncMock()
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.blocking_admit_gate",
        gate,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.submit_nested_dispatch",
        submit,
    )
    job = AutoJob(
        job_id="j-nondir",
        thread_id="6204",
        turn_number=1,
        subject="implement",
        body="contract: implement\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="answer",
    )
    asyncio.run(process_job(job, bus=bus))
    gate.assert_awaited_once()
    submit.assert_not_awaited()


def test_effective_contract_compose_unchanged_for_scoped_directive() -> None:
    body = "TYPE: DIRECTIVE\ndensity: dense\n## Scope\nlibs/foo\n"
    assert effective_contract("answer", body) == "implement"
    assert has_actionable_scope(body)


_SCOPED_DIRECTIVE = (
    "TYPE: DIRECTIVE\n"
    "density: dense\n"
    "arc: agent-bus:6205 / fable-vision-permeation\n"
    "## Scope\n"
    "libs/foo\n"
)


def test_has_vision_field_presence_only() -> None:
    assert not has_vision_field(_SCOPED_DIRECTIVE)
    assert has_vision_field(_SCOPED_DIRECTIVE + "vision: pillar-3\n")
    assert has_vision_field(
        _SCOPED_DIRECTIVE + "vision: mechanical — wiring-only substrate change\n"
    )


@pytest.mark.asyncio
async def test_blocking_admit_gate_missing_vision_blocks_implement() -> None:
    fetch = AsyncMock(return_value=[])
    job = AutoJob(
        job_id="j-vision",
        thread_id="6205",
        turn_number=1,
        subject="vision gate",
        body=_SCOPED_DIRECTIVE,
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
            fetch,
        )
        gate_out = await blocking_admit_gate(
            job,
            client=_bus_client(),
            queue=MagicMock(),
        )
        blocked = gate_out.blocked
    assert blocked is not None
    assert blocked["terminal_status"] == "status:blocked"
    assert "vision_field_missing" in blocked["summary"]
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_blocking_admit_gate_missing_vision_blocks_investigate() -> None:
    fetch = AsyncMock(return_value=[])
    job = AutoJob(
        job_id="j-vision-inv",
        thread_id="6205",
        turn_number=1,
        subject="vision gate investigate",
        body=_SCOPED_DIRECTIVE,
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="grok-4.5",
        desired_effort="medium",
        contract="investigate",
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
            fetch,
        )
        gate_out = await blocking_admit_gate(
            job,
            client=_bus_client(),
            queue=MagicMock(),
        )
        blocked = gate_out.blocked
    assert blocked is not None
    assert "vision_field_missing" in blocked["summary"]
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_blocking_admit_gate_with_vision_passes() -> None:
    job = AutoJob(
        job_id="j-vision-ok",
        thread_id="6205",
        turn_number=1,
        subject="vision ok",
        body=_SCOPED_DIRECTIVE + "vision: pillar-3 — serves HTTP substrate\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_status",
            AsyncMock(return_value="active"),
        )
        mp.setattr(
            "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
            AsyncMock(return_value=[]),
        )
        result = await blocking_admit_gate(
            job,
            client=_bus_client(),
            queue=MagicMock(),
        )
    assert result.blocked is None


@pytest.mark.asyncio
async def test_blocking_admit_gate_closed_thread_refuses(
    _capture_events: list[tuple[str, dict[str, object]]],
) -> None:
    fetch_turns = AsyncMock(return_value=[])
    fetch_status = AsyncMock(return_value="closed")
    job = AutoJob(
        job_id="j-closed",
        thread_id="5899",
        turn_number=1,
        subject="closed thread",
        body=_SCOPED_DIRECTIVE + "vision: mechanical — closed thread refuse\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_status",
            fetch_status,
        )
        mp.setattr(
            "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
            fetch_turns,
        )
        gate_out = await blocking_admit_gate(
            job,
            client=_bus_client(),
            queue=MagicMock(),
        )
        blocked = gate_out.blocked
    assert blocked is not None
    assert blocked["terminal_status"] == "status:blocked"
    assert "thread_terminal_status_refused" in blocked["summary"]
    fetch_turns.assert_not_awaited()
    assert any(
        sig == "frontier.sdk.auto.thread_status_refused"
        and payload.get("status") == "closed"
        for sig, payload in _capture_events
    )


@pytest.mark.asyncio
async def test_blocking_admit_gate_blocked_thread_refuses(
    _capture_events: list[tuple[str, dict[str, object]]],
) -> None:
    fetch_turns = AsyncMock(return_value=[])
    fetch_status = AsyncMock(return_value="blocked")
    job = AutoJob(
        job_id="j-blocked",
        thread_id="5899",
        turn_number=1,
        subject="blocked thread",
        body=_SCOPED_DIRECTIVE + "vision: mechanical — blocked thread refuse\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_status",
            fetch_status,
        )
        mp.setattr(
            "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
            fetch_turns,
        )
        gate_out = await blocking_admit_gate(
            job,
            client=_bus_client(),
            queue=MagicMock(),
        )
        blocked = gate_out.blocked
    assert blocked is not None
    assert blocked["terminal_status"] == "status:blocked"
    assert "thread_terminal_status_refused" in blocked["summary"]
    fetch_turns.assert_not_awaited()
    assert any(
        sig == "frontier.sdk.auto.thread_status_refused"
        and payload.get("status") == "blocked"
        for sig, payload in _capture_events
    )


@pytest.mark.asyncio
async def test_blocking_admit_gate_verify_non_directive_exempt() -> None:
    fetch = AsyncMock(return_value=[])
    job = AutoJob(
        job_id="j-verify",
        thread_id="6205",
        turn_number=1,
        subject="verify",
        body="contract: verify\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="verify",
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_status",
            AsyncMock(return_value=None),
        )
        mp.setattr(
            "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
            fetch,
        )
        result = await blocking_admit_gate(
            job,
            client=_bus_client(),
            queue=MagicMock(),
        )
    assert result.blocked is None
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_blocking_admit_gate_mechanical_vision_passes() -> None:
    job = AutoJob(
        job_id="j-mechanical",
        thread_id="6205",
        turn_number=1,
        subject="mechanical vision",
        body=_SCOPED_DIRECTIVE + "vision: mechanical — presence-only wiring\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_status",
            AsyncMock(return_value="active"),
        )
        mp.setattr(
            "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
            AsyncMock(return_value=[]),
        )
        result = await blocking_admit_gate(
            job,
            client=_bus_client(),
            queue=MagicMock(),
        )
    assert result.blocked is None


@pytest.mark.asyncio
async def test_blocking_admit_gate_wrong_pillar_still_passes_presence_only() -> None:
    job = AutoJob(
        job_id="j-wrong-pillar",
        thread_id="6205",
        turn_number=1,
        subject="wrong pillar",
        body=_SCOPED_DIRECTIVE + "vision: pillar-9 — serves unrelated galaxy\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_status",
            AsyncMock(return_value="active"),
        )
        mp.setattr(
            "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
            AsyncMock(return_value=[]),
        )
        result = await blocking_admit_gate(
            job,
            client=_bus_client(),
            queue=MagicMock(),
        )
    assert result.blocked is None
