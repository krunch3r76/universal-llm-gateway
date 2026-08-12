"""Tests for frontier.sdk.closeout.partial_work.production_specimen wake instrument."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.git_integration_worker.cursor_auto.nested_outcome import (
    relay_closeout_outcome,
)
from services.git_integration_worker.cursor_auto.nested_sdk import (
    post_operator_closeout,
)
from services.git_integration_worker.cursor_auto.partial_work_production_specimen_events import (
    NATURAL_SPECIMEN_CLASSIFICATION,
    SIGNAL,
    FrontierSdkCloseoutPartialWorkProductionSpecimen,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob


def _checkpoint_body(status: str, *, claim: str = "partial") -> str:
    return (
        f"TYPE: CLOSEOUT\n"
        f"status: {status}\n"
        f"checkpoint: nothing_authored\n\n"
        f"## §2 closeout\n\n"
        f"**status_claim:** {claim}\n\n"
        f"**checkpoint_claim:** nothing_authored\n"
    )


def _partial_capture_body() -> str:
    return (
        "TYPE: CLOSEOUT\n"
        "status: partial:capture\n"
        "checkpoint: nothing_authored\n\n"
        "## §2 closeout\n\n"
        "**status_claim:** partial\n\n"
        "**checkpoint_claim:** nothing_authored\n"
    )


def _complete_body() -> str:
    return (
        "TYPE: CLOSEOUT\n"
        "status: complete\n"
        "checkpoint: nothing_authored\n\n"
        "## §2 closeout\n\n"
        "**status_claim:** complete\n\n"
        "**checkpoint_claim:** nothing_authored\n"
    )


@pytest.fixture
def relay_job() -> AutoJob:
    return AutoJob(
        job_id="j-specimen",
        thread_id="6655",
        turn_number=2489,
        subject="partial work specimen",
        body="TYPE: DIRECTIVE\nintent: test\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
    )


@pytest.mark.asyncio
async def test_post_operator_closeout_emits_specimen_on_partial_work(
    monkeypatch: pytest.MonkeyPatch,
    relay_job: AutoJob,
) -> None:
    emitted: list[dict[str, object]] = []

    def _capture(**kwargs: object) -> None:
        emitted.append(dict(kwargs))

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.partial_work_production_specimen_events.emit_partial_work_production_specimen",
        _capture,
    )
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    result = await post_operator_closeout(
        relay_job,
        status="partial:work",
        dispatch_id="auto-specimen01",
        model_id="cursor/composer-2.5",
        sdk_body=None,
        closeout_body=_checkpoint_body("partial:work"),
        closeout_source="section2_sidecar",
        bus=bus,
        skip_outbox_persist=True,
    )

    assert result["ok"] is True
    assert len(emitted) == 1
    payload = emitted[0]
    assert payload["dispatch_id"] == "auto-specimen01"
    assert payload["envelope_turn"] == 2489
    assert payload["thread_id"] == "6655"
    assert payload["closeout_source"] == "section2_sidecar"
    assert payload["contract"] == "implement"
    assert payload["replay_mode"] is False


def test_production_specimen_event_factory_payload() -> None:
    event = FrontierSdkCloseoutPartialWorkProductionSpecimen(
        dispatch_id="auto-specimen01",
        envelope_turn=2489,
        thread_id="6655",
        closeout_source="section2_sidecar",
        contract="implement",
        replay_mode=False,
        natural_specimen_classification=NATURAL_SPECIMEN_CLASSIFICATION,
        code_ref="test",
        schema_version=1,
    )
    assert event.signal == SIGNAL
    assert event.payload["natural_specimen_classification"] == "unavailable"


@pytest.mark.asyncio
async def test_post_operator_closeout_does_not_emit_on_partial_capture_or_complete(
    monkeypatch: pytest.MonkeyPatch,
    relay_job: AutoJob,
) -> None:
    emitted: list[object] = []

    def _capture(**kwargs: object) -> None:
        emitted.append(kwargs)

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.partial_work_production_specimen_events.emit_partial_work_production_specimen",
        _capture,
    )
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    await post_operator_closeout(
        relay_job,
        status="partial:capture",
        dispatch_id="auto-capture01",
        model_id="cursor/composer-2.5",
        sdk_body=None,
        closeout_body=_partial_capture_body(),
        closeout_source="section2_sidecar",
        bus=bus,
        skip_outbox_persist=True,
    )
    await post_operator_closeout(
        relay_job,
        status="complete",
        dispatch_id="auto-complete01",
        model_id="cursor/composer-2.5",
        sdk_body=None,
        closeout_body=_complete_body(),
        closeout_source="wrapper",
        bus=bus,
        skip_outbox_persist=True,
    )

    assert len(emitted) == 0


@pytest.mark.asyncio
async def test_post_operator_closeout_skips_specimen_on_replay_mode(
    monkeypatch: pytest.MonkeyPatch,
    relay_job: AutoJob,
) -> None:
    emitted: list[object] = []

    def _capture(**kwargs: object) -> None:
        emitted.append(kwargs)

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.partial_work_production_specimen_events.emit_partial_work_production_specimen",
        _capture,
    )
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    await post_operator_closeout(
        relay_job,
        status="partial:work",
        dispatch_id="auto-replay01",
        model_id="cursor/composer-2.5",
        sdk_body=None,
        closeout_body=_checkpoint_body("partial:work"),
        closeout_source="section2_sidecar",
        bus=bus,
        skip_outbox_persist=True,
        replay_mode=True,
    )

    assert len(emitted) == 0


def test_relay_path_partial_work_json_resolves_to_partial_work_status() -> None:
    """End-to-end classification: work JSON → partial:work measurement token."""
    from services.git_integration_worker.cursor_auto.closeout_relay import (
        select_closeout_relay_payload,
    )

    structured = json.dumps(
        {
            "schema_version": 1,
            "status": "partial",
            "status_incomplete_class": "work",
            "work_outcome": "checks_failed",
            "capture_status": "partial",
        }
    )
    sidecar = (
        "## §2 closeout\n\n"
        "**status_claim:** complete\n\n"
        f"## structured_closeout_full\n\n{structured}"
    )
    payload = select_closeout_relay_payload(
        sdk_body=json.dumps(
            {
                "schema_version": 1,
                "status": "complete",
                "work_outcome": "shipped",
                "body_relocated": {"uri": "cortex://notes/system/threads/test.md"},
            }
        ),
        sidecar_text=sidecar,
        ledger_status="completed",
        dispatch_id="auto-work-path",
    )
    assert payload.status == "partial:work"


def test_relay_path_partial_capture_json_resolves_to_partial_capture_status() -> None:
    from services.git_integration_worker.cursor_auto.closeout_relay import (
        select_closeout_relay_payload,
    )

    structured = json.dumps(
        {
            "schema_version": 1,
            "status": "partial",
            "status_incomplete_class": "capture",
            "work_outcome": "verified",
            "capture_status": "partial",
        }
    )
    sidecar = (
        "## §2 closeout\n\n"
        "**status_claim:** partial\n\n"
        f"## structured_closeout_full\n\n{structured}"
    )
    payload = select_closeout_relay_payload(
        sdk_body='{"status":"partial","status_incomplete_class":"capture"}',
        sidecar_text=sidecar,
        ledger_status="completed",
        dispatch_id="auto-capture-path",
    )
    assert payload.status == "partial:capture"


def _partial_work_sidecar() -> str:
    structured = json.dumps(
        {
            "schema_version": 1,
            "status": "partial",
            "status_incomplete_class": "work",
            "work_outcome": "checks_failed",
            "capture_status": "partial",
        }
    )
    return (
        "TYPE: CLOSEOUT\n"
        "status: partial:work\n"
        "checkpoint: nothing_authored\n\n"
        "## §2 closeout\n\n"
        "**status_claim:** partial\n\n"
        "**checkpoint_claim:** nothing_authored\n\n"
        f"## structured_closeout_full\n\n{structured}"
    )


def _relay_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub tree/wake I/O so ``relay_closeout_outcome`` reaches ``post_operator_closeout``."""
    from services.git_integration_worker.cursor_auto.closeout_relay import (
        CloseoutRelayPayload,
    )
    from services.git_integration_worker.cursor_auto.closeout_tree_state import (
        CloseoutTreeState,
    )
    from services.git_integration_worker.cursor_auto.lane_a_checkpoint import (
        TreeResidueSnapshot,
    )

    async def _passthrough_promote(
        payload: CloseoutRelayPayload, **_k: object
    ) -> CloseoutRelayPayload:
        return payload

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.read_repo_closeout_sidecar",
        lambda *_a, **_k: _partial_work_sidecar(),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.promote_clamped_closeout_to_cortex",
        _passthrough_promote,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.derive_tree_residue",
        lambda **_k: TreeResidueSnapshot(count=0, authored_paths=()),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.compute_closeout_tree_state",
        lambda **_k: CloseoutTreeState(
            checkpoint="nothing_authored",
            deployment_state=None,
            plane_line="plane: local-master=probe lane-b=absent origin=absent",
            plane_discrepancy=None,
        ),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.config.load_config",
        lambda: MagicMock(source_repo=Path("/tmp/6655-specimen-repo")),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.cse_wake_delivery.pay_wake_unit",
        AsyncMock(
            return_value={
                "wake": {"ok": True},
                "delivery": {"ok": True},
            }
        ),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.maybe_post_substrate_feedback",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.append_journal_entry",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.emit_sdk_closeout_relayed",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_sdk._relay_pause_s",
        lambda: 0,
    )


@pytest.mark.asyncio
async def test_relay_closeout_outcome_emits_specimen_on_partial_work(
    monkeypatch: pytest.MonkeyPatch,
    relay_job: AutoJob,
) -> None:
    """Full-path integration: relay → post_operator_closeout → specimen emit."""
    emitted: list[dict[str, object]] = []

    def _capture(**kwargs: object) -> None:
        emitted.append(dict(kwargs))

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.partial_work_production_specimen_events.emit_partial_work_production_specimen",
        _capture,
    )
    _relay_stubs(monkeypatch)
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    result = await relay_closeout_outcome(
        relay_job,
        client=bus,
        queue=MagicMock(),
        dispatch_id="auto-relay-specimen",
        model={"resolved_model_id": "cursor/composer-2.5"},
        effort={},
        gate_plan={},
        contract_info={"disposition_hint": "implemented"},
        sdk_body=json.dumps(
            {
                "schema_version": 1,
                "status": "complete",
                "work_outcome": "shipped",
                "body_relocated": {"uri": "cortex://notes/system/threads/test.md"},
            }
        ),
        terminal_status="completed",
        nest_under=None,
        execution_id="exec-auto-relay-specimen",
    )

    assert result["ok"] is True
    assert len(emitted) == 1
    payload = emitted[0]
    assert payload["dispatch_id"] == "auto-relay-specimen"
    assert payload["envelope_turn"] == relay_job.turn_number
    assert payload["thread_id"] == relay_job.thread_id
    assert payload["contract"] == "implement"
    assert payload["replay_mode"] is False
