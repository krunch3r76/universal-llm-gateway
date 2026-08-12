"""Hermetic marks for silence-family victim classes (arc 6929 L5)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_bundles.cdp_model_endpoint import result_from_snapshot

from services.git_integration_worker.cursor_auto.closeout_outbox import (
    CloseoutOutboxStore,
)
from services.git_integration_worker.cursor_auto.hop_cadence import (
    CapacityGateResult,
    fire_hop_for_decision,
)
from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
    HopDecision,
    StandingHandoffFreshness,
)
from services.git_integration_worker.cursor_auto.job_ledger import (
    AutoJobLedger,
    get_ledger,
)
from services.git_integration_worker.cursor_auto.job_reconcile import (
    reconcile_open_auto_jobs,
)
from services.git_integration_worker.cursor_auto.queue import (
    get_queue,
    reset_queue_for_tests,
)


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "CURSOR_AUTO_HOP_WATCHES_PATH",
        str(tmp_path / "hop_cadence_watches.json"),
    )
    AutoJobLedger.reset_for_tests()
    CloseoutOutboxStore.reset_for_tests()
    reset_queue_for_tests(durable=True)
    yield
    AutoJobLedger.reset_for_tests()
    CloseoutOutboxStore.reset_for_tests()


def test_never_dispatched_soft_fail_marks_bus_unposted() -> None:
    """Soft-fail bus reply ⇒ durable bus_notify_pending + unposted signal."""
    job = get_queue().enqueue(
        thread_id="6929",
        turn_number=1,
        subject="silence family",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    get_queue().claim_next()
    emit = MagicMock()

    with (
        patch(
            "services.git_integration_worker.cursor_auto.job_reconcile."
            "post_queue_owner_restart_terminal",
            new_callable=AsyncMock,
            return_value={
                "ok": False,
                "status_code": 599,
                "terminal_status": "status:failed",
            },
        ),
        patch(
            "services.git_integration_worker.cursor_auto.job_reconcile."
            "emit_queue_owner_restart_bus_unposted",
            emit,
        ),
    ):
        terminalized = asyncio.run(reconcile_open_auto_jobs(post_bus=True))

    assert len(terminalized) == 1
    record = get_ledger().read_record_json(job.job_id)
    assert record.get("bus_notify_pending") is True
    assert record.get("bus_notify_mark") == "queue_owner_restart_death"
    emit.assert_called_once()
    assert emit.call_args.kwargs["job_id"] == job.job_id
    assert emit.call_args.kwargs["status_code"] == 599


@pytest.mark.asyncio
async def test_hop_fire_missing_execution_id_emits_successor_never_ran(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claim-only hop (no joinable id) must not mark_hop_fired."""
    from services.git_integration_worker.cursor_auto import hop_cadence as cadence_mod
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)
    fired: list[tuple] = []
    failed: list[tuple] = []
    emit = MagicMock()

    async def _hop_no_id(job, *, queue, incumbent=None):
        return {"ok": True, "reason": "continuity_hop_cdp_commissioned"}

    monkeypatch.setattr(cadence_mod, "run_continuity_hop_concurrent", _hop_no_id)
    monkeypatch.setattr(
        cadence_mod, "capacity_blocks_hop", lambda **_: CapacityGateResult.fail_open()
    )
    monkeypatch.setattr(
        cadence_mod,
        "mark_hop_fired",
        lambda *a, **k: fired.append((a, k)),
    )
    monkeypatch.setattr(
        cadence_mod,
        "mark_hop_failed",
        lambda *a, **k: failed.append((a, k)),
    )
    monkeypatch.setattr(
        cadence_mod,
        "emit_succession_claim_missing_execution_id",
        emit,
    )
    monkeypatch.setattr(
        cadence_mod,
        "assess_standing_handoff",
        lambda tid: StandingHandoffFreshness(
            "current", f"cortex://x/{tid}.md", None, 1.0
        ),
    )

    decision = HopDecision(
        thread_id="T-silence-no-exec",
        action="fire",
        reason="age_exceeded",
        age_s=2000.0,
        threshold_s=1500.0,
        signal="watch_seated_at",
    )
    outcome = await fire_hop_for_decision(
        decision,
        queue=q,
        row={"from_agent": "web-anthropic", "registration_id": "reg-1"},
    )

    assert outcome["ok"] is False
    assert outcome.get("execution_id") is None
    assert fired == []
    assert len(failed) == 1
    assert failed[0][0][0] == "T-silence-no-exec"
    assert failed[0][1]["reason"] == "missing_execution_id"
    emit.assert_called_once()
    assert emit.call_args.kwargs["thread_id"] == "T-silence-no-exec"


@pytest.mark.asyncio
async def test_liveness_probe_swallow_emits_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Probe exception keeps fail-open but surfaces liveness_probe + signal."""
    from services.git_integration_worker.cursor_auto import hop_cadence as cadence_mod
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)
    emit = MagicMock()

    def _boom():
        raise TimeoutError("snap timed out")

    async def _hop_ok(job, *, queue, incumbent=None):
        return {
            "ok": True,
            "reason": "continuity_hop_cdp_commissioned",
            "execution_id": "exec-probe-1",
        }

    monkeypatch.setattr(cadence_mod, "run_continuity_hop_concurrent", _hop_ok)
    monkeypatch.setattr(
        cadence_mod, "capacity_blocks_hop", lambda **_: CapacityGateResult.fail_open()
    )
    monkeypatch.setattr(cadence_mod, "mark_hop_fired", lambda *a, **k: None)
    monkeypatch.setattr(cadence_mod, "emit_liveness_probe_failed", emit)
    monkeypatch.setattr(
        cadence_mod,
        "assess_standing_handoff",
        lambda tid: StandingHandoffFreshness(
            "current", f"cortex://x/{tid}.md", None, 1.0
        ),
    )
    monkeypatch.setattr(
        cadence_mod,
        "refuse_cadence_hop_for_live_seat",
        lambda row, snap: (False, None, {}),
    )

    decision = HopDecision(
        thread_id="T-silence-probe",
        action="fire",
        reason="age_exceeded",
        age_s=2000.0,
        threshold_s=1500.0,
        signal="watch_seated_at",
    )
    outcome = await fire_hop_for_decision(
        decision,
        queue=q,
        row={"from_agent": "web-anthropic"},
        snapshot_reader=_boom,
    )

    assert outcome["ok"] is True
    assert outcome["liveness_probe"]["fail_open"] is True
    emit.assert_called_once()
    assert "timed out" in emit.call_args.kwargs["error"]


def test_run_cdp_generate_proof_weekly_limit_banner_not_a_seat() -> None:
    """Weekly-limit harvest ⇒ ok=False, stall_stage=weekly_limit, banner mark."""
    result = result_from_snapshot(
        snapshot={
            "status": "completed",
            "completion_phase": "terminal",
            "body": "You've hit your weekly limit. Try again next week.",
            "attested_model": "Model: Opus",
            "harvest_provenance": "chat",
        },
        execution_id="disp-wl",
        satellite_execution_id="sat-wl",
        prompt_uri="cortex://notes/system/threads/wl-prompt.md",
        picker_model="opus-5",
    )
    assert result is not None
    assert result.ok is False
    assert result.stall_stage == "weekly_limit"
    assert result.extras.get("mark") == "banner_not_a_seat"
