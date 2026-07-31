"""Tests for frontier.sdk.closeout.relayed and relay-path failure isolation."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.git_integration_worker.cursor_auto.nested_outcome import (
    relay_closeout_outcome,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_sdk_events import FrontierSdkCloseoutRelayed


@pytest.fixture
def relay_job() -> AutoJob:
    return AutoJob(
        job_id="j-relay",
        thread_id="6221",
        turn_number=3,
        subject="DIRECTIVE story wire",
        body=(
            "TYPE: DIRECTIVE\n"
            "intent: Put the story on the spine.\n"
            "## Scope\nservices/git_integration_worker/\n"
        ),
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
    )


@pytest.mark.asyncio
async def test_relay_closeout_emits_relayed_once_with_full_envelope(
    monkeypatch: pytest.MonkeyPatch,
    relay_job: AutoJob,
) -> None:
    emitted: list[FrontierSdkCloseoutRelayed] = []

    def _capture(**kwargs: object) -> None:
        emitted.append(FrontierSdkCloseoutRelayed(**kwargs))  # type: ignore[arg-type]

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.emit_sdk_closeout_relayed",
        _capture,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.read_repo_closeout_sidecar",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.post_operator_closeout",
        AsyncMock(return_value={"ok": True, "status_code": 200}),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.post_operator_wake",
        AsyncMock(return_value={"ok": True, "status_code": 200}),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.maybe_post_substrate_feedback",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.append_journal_entry",
        lambda **_k: None,
    )

    queue = MagicMock()
    result = await relay_closeout_outcome(
        relay_job,
        client=AsyncMock(),
        queue=queue,
        dispatch_id="auto-relay01",
        model={"resolved_model_id": "cursor/composer-2.5"},
        effort={},
        gate_plan={},
        contract_info={"disposition_hint": "implemented"},
        sdk_body='{"status":"complete"}',
        terminal_status="completed",
        nest_under=None,
        execution_id="exec-auto-relay01",
    )

    assert result["ok"] is True
    assert len(emitted) == 1
    event = emitted[0]
    assert event.signal == "frontier.sdk.closeout.relayed"
    assert event.payload["dispatch_id"] == "auto-relay01"
    assert event.payload["thread_id"] == "6221"
    assert event.payload["execution_id"] == "exec-auto-relay01"
    assert event.payload["closeout_status"]
    assert event.payload["receipt_path"].endswith("tmp/reviews/closeouts/auto-relay01.md")
    assert event.payload["asked_by"] == "web-anthropic"
    assert event.payload["purpose"] == "Put the story on the spine."
    assert event.payload["story_id"] == "auto-relay01"


@pytest.mark.asyncio
async def test_relay_closeout_purpose_degrades_without_raising(
    monkeypatch: pytest.MonkeyPatch,
    relay_job: AutoJob,
) -> None:
    relay_job = AutoJob(
        job_id=relay_job.job_id,
        thread_id=relay_job.thread_id,
        turn_number=relay_job.turn_number,
        subject=relay_job.subject,
        body="TYPE: DIRECTIVE\n## Scope\nonly scope\n",
        from_agent=relay_job.from_agent,
        to_agent=relay_job.to_agent,
        desired_model=relay_job.desired_model,
        desired_effort=relay_job.desired_effort,
        contract=relay_job.contract,
    )
    captured: dict[str, str] = {}

    def _capture(**kwargs: object) -> None:
        captured["purpose"] = str(kwargs.get("purpose"))

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.emit_sdk_closeout_relayed",
        _capture,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.read_repo_closeout_sidecar",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.post_operator_closeout",
        AsyncMock(return_value={"ok": True, "status_code": 200}),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.post_operator_wake",
        AsyncMock(return_value={"ok": True, "status_code": 200}),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.maybe_post_substrate_feedback",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.append_journal_entry",
        lambda **_k: None,
    )

    await relay_closeout_outcome(
        relay_job,
        client=AsyncMock(),
        queue=MagicMock(),
        dispatch_id="auto-thin01",
        model={"resolved_model_id": "cursor/composer-2.5"},
        effort={},
        gate_plan={},
        contract_info={"disposition_hint": "implemented"},
        sdk_body='{"status":"complete"}',
        terminal_status="completed",
        nest_under=None,
        execution_id="exec-auto-thin01",
    )
    assert captured["purpose"] == "(unstated)"


def test_story_wire_envelope_raise_does_not_fail_relay_closeout(
    monkeypatch: pytest.MonkeyPatch,
    relay_job: AutoJob,
) -> None:
    """AC6: envelope construction/emit raise must not fail the relay."""

    def _boom(**_kwargs: object) -> None:
        raise RuntimeError("forced envelope failure")

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.build_association_envelope",
        _boom,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.read_repo_closeout_sidecar",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.post_operator_closeout",
        AsyncMock(return_value={"ok": True, "status_code": 200}),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.post_operator_wake",
        AsyncMock(return_value={"ok": True, "status_code": 200}),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.maybe_post_substrate_feedback",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.append_journal_entry",
        lambda **_k: None,
    )

    result = asyncio.run(
        relay_closeout_outcome(
            relay_job,
            client=AsyncMock(),
            queue=MagicMock(),
            dispatch_id="auto-failiso",
            model={"resolved_model_id": "cursor/composer-2.5"},
            effort={},
            gate_plan={},
            contract_info={"disposition_hint": "implemented"},
            sdk_body='{"status":"complete"}',
            terminal_status="completed",
            nest_under=None,
            execution_id="exec-auto-failiso",
        )
    )
    assert result["ok"] is True


def test_completed_event_carries_optional_association_fields() -> None:
    from services.git_integration_worker.cursor_sdk_events import (
        FrontierSdkWorkerCompleted,
    )

    bare = FrontierSdkWorkerCompleted(
        dispatch_id="d1",
        thread_id="t1",
        execution_id="e1",
        duration_s=1.0,
        tool_call_count=0,
        result_bytes=0,
        outcome="ok",
        resolved_model="cursor/composer-2.5",
    )
    assert "asked_by" not in bare.payload

    stamped = FrontierSdkWorkerCompleted(
        dispatch_id="req1-abcd1234",
        thread_id="t1",
        execution_id="e1",
        duration_s=1.0,
        tool_call_count=0,
        result_bytes=0,
        outcome="ok",
        resolved_model="cursor/composer-2.5",
        asked_by="cursor",
        purpose="(unstated)",
        story_id="req1",
    )
    assert stamped.payload["story_id"] == "req1"
    assert stamped.payload["asked_by"] == "cursor"
    assert stamped.payload["purpose"] == "(unstated)"
