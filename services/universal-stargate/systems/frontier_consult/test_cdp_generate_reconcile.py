"""Regression tests for CDP generate reconcile + finalize idempotency."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from claude_bundles.cdp_model_endpoint import (
    CdpGenerateResult,
    has_proof,
    result_from_snapshot,
)

from systems.frontier_consult import cdp_generate_reconcile as reconcile
from systems.frontier_consult.cdp_dispatch_envelope import (
    record_cdp_admit,
    reset_cdp_dispatch_envelope_for_tests,
)
from systems.frontier_consult.cdp_generate_inflight_ledger import _connect
from systems.frontier_consult.cdp_generate_reconcile import (
    finalize_cdp_generate,
    max_open_leg_s,
    reset_cdp_generate_reconcile_for_tests,
    upsert_inflight_leg,
)


@pytest.fixture(autouse=True)
def _reset_ledger() -> None:
    reset_cdp_generate_reconcile_for_tests()
    reset_cdp_dispatch_envelope_for_tests()


@pytest.fixture(autouse=True)
def _no_live_authorship_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        reconcile,
        "seated_authorship_on_thread",
        AsyncMock(return_value=False),
    )


def _proof_snapshot() -> dict[str, Any]:
    return {
        "status": "completed",
        "completion_phase": "terminal",
        "archive_uri": "cortex://notes/system/artifacts/cdp/proof.md",
        "body": "harvest",
        "attested_model": "Model: Opus 5",
    }


def _running_snapshot() -> dict[str, Any]:
    return {
        "status": "running",
        "completion_phase": "running",
        "stall_stage": None,
    }


def test_has_proof_fail_closed_without_archive() -> None:
    snap = {"status": "completed", "completion_phase": "terminal"}
    assert has_proof(snap) is False


def test_result_from_snapshot_running_returns_none() -> None:
    assert (
        result_from_snapshot(
            snapshot=_running_snapshot(),
            execution_id="exec-1",
            satellite_execution_id="sat-1",
            prompt_uri="cortex://p.md",
            picker_model="opus-5",
        )
        is None
    )


def test_result_from_snapshot_completed_without_proof_stalls() -> None:
    result = result_from_snapshot(
        snapshot={"status": "completed", "completion_phase": "terminal"},
        execution_id="exec-1",
        satellite_execution_id="sat-1",
        prompt_uri="cortex://p.md",
        picker_model="opus-5",
    )
    assert result is not None
    assert result.ok is False
    assert result.stall_stage == "completed_without_proof"
    assert "attested_model" in (result.error or "")
    assert "without archive_uri" not in (result.error or "")


def test_result_from_snapshot_review_induction_keeps_waiting() -> None:
    """purpose=review with attested induction body must not finalize (a:37156 F1)."""
    induction = (
        "I'll use the reasoning-posture skill and review the packet next.\n"
        "Loading skills…"
    )
    snapshot = {
        "status": "completed",
        "completion_phase": "terminal",
        "attested_model": "claude-opus-4-5",
        "body": induction,
        "archive_uri": "cortex://notes/system/threads/cdp-ask-archive-induction.md",
    }
    assert has_proof(snapshot) is True
    assert (
        result_from_snapshot(
            snapshot=snapshot,
            execution_id="exec-review-f1",
            satellite_execution_id="sat-review-f1",
            prompt_uri="cortex://p.md",
            picker_model="opus-5",
            purpose="review",
        )
        is None
    )
    # Non-review purposes still finalize attested non-chrome bodies.
    ok_result = result_from_snapshot(
        snapshot=snapshot,
        execution_id="exec-ask",
        satellite_execution_id="sat-ask",
        prompt_uri="cortex://p.md",
        picker_model="opus-5",
        purpose="ask",
    )
    assert ok_result is not None
    assert ok_result.ok is True


def test_result_from_snapshot_review_with_verdict_finalizes() -> None:
    body = (
        "## Merits\n"
        "Gate holds.\n"
        "VERDICT: PASS\n"
    )
    snapshot = {
        "status": "completed",
        "completion_phase": "terminal",
        "attested_model": "claude-opus-4-5",
        "body": body,
        "archive_uri": "cortex://notes/system/threads/cdp-ask-archive-verdict.md",
    }
    result = result_from_snapshot(
        snapshot=snapshot,
        execution_id="exec-review-pass",
        satellite_execution_id="sat-review-pass",
        prompt_uri="cortex://p.md",
        picker_model="opus-5",
        purpose="review",
    )
    assert result is not None
    assert result.ok is True


def test_inflight_leg_persists_purpose(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    upsert_inflight_leg(
        execution_id="exec-purpose",
        request_id="req-1",
        thread_id="99",
        pointer_turn=1,
        caller_agent="cursor",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=120.0,
        purpose="review",
    )
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT purpose FROM cdp_inflight_leg WHERE execution_id=?",
            ("exec-purpose",),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["purpose"] == "review"


def test_result_from_snapshot_completed_without_proof_carries_deliverable() -> None:
    result = result_from_snapshot(
        snapshot={
            "status": "completed",
            "completion_phase": "terminal",
            "archive_uri": "cortex://notes/system/threads/cdp-ask-archive-new.md",
            "body": "SKILLS_PROBE_OK",
        },
        execution_id="exec-2",
        satellite_execution_id="sat-2",
        prompt_uri="cortex://p.md",
        picker_model="fable-5",
    )
    assert result is not None
    assert result.archive_uri == "cortex://notes/system/threads/cdp-ask-archive-new.md"
    assert result.extras.get("deliverable_present_unproven") is True
    assert "do not blind re-dispatch" in str(result.extras.get("recovery"))


def test_result_from_snapshot_failed_unknown_is_observer_unverified() -> None:
    result = result_from_snapshot(
        snapshot={
            "status": "failed",
            "stall_stage": "unknown",
            "error": "wait_assistant_reply timed out",
            "url": "https://claude.ai/cowork/cse_abc",
            "satellite_execution_id": "sat-uv",
        },
        execution_id="exec-uv",
        satellite_execution_id="sat-uv",
        prompt_uri="cortex://p.md",
        picker_model="fable-5",
    )
    assert result is not None
    assert result.ok is False
    assert result.stall_stage == "observer_unverified"
    assert result.extras.get("chat_url") == "https://claude.ai/cowork/cse_abc"


def _age_leg_past_horizon(execution_id: str, *, max_wall_s: float = 1800.0) -> None:
    old = (
        datetime.now(UTC) - timedelta(seconds=max_open_leg_s(max_wall_s) + 10)
    ).isoformat()
    conn = _connect()
    try:
        conn.execute(
            "UPDATE cdp_inflight_leg SET admitted_at=? WHERE execution_id=?",
            (old, execution_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_max_open_leg_s_floor() -> None:
    assert max_open_leg_s(300.0) >= 3600.0


@pytest.mark.asyncio
async def test_finalize_unconfirmed_wall_leaves_proof_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unconfirmed wall abort must not stamp proof_emitted; confirmed wall does."""
    from cdp_ask.unverifiable import WALL_CLOCK_EXCEEDED_ABORT_UNCONFIRMED

    published: list[str] = []

    def _capture(factory: Any, **_kwargs: Any) -> None:
        published.append(factory.__name__)

    async def _terminate(**_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", _capture)
    monkeypatch.setattr(reconcile, "terminal_event_exists", lambda _eid: False)
    monkeypatch.setattr(
        "systems.frontier_consult.handoff.terminate_handoff_dispatch",
        _terminate,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        AsyncMock(return_value=True),
    )

    upsert_inflight_leg(
        execution_id="exec-wall-unconfirmed",
        request_id="req-wall-u",
        thread_id="12286",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    await finalize_cdp_generate(
        result=CdpGenerateResult(
            ok=False,
            body="",
            execution_id="exec-wall-unconfirmed",
            satellite_execution_id="sat-wall-u",
            prompt_uri="cortex://p.md",
            picker_model="opus-5",
            stall_stage=WALL_CLOCK_EXCEEDED_ABORT_UNCONFIRMED,
            error="CDP generate no progress for max_wall_s=10",
        ),
        request_id="req-wall-u",
        thread_id="12286",
        to_agent="cursor-sdk-test",
        pointer_turn=1,
        via="worker",
    )
    leg = reconcile.read_inflight_leg("exec-wall-unconfirmed")
    assert leg is not None
    assert leg.proof_emitted is False
    assert "CdpGenerateStalled" in published

    upsert_inflight_leg(
        execution_id="exec-wall-confirmed",
        request_id="req-wall-c",
        thread_id="12286",
        pointer_turn=2,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    await finalize_cdp_generate(
        result=CdpGenerateResult(
            ok=False,
            body="",
            execution_id="exec-wall-confirmed",
            satellite_execution_id="sat-wall-c",
            prompt_uri="cortex://p.md",
            picker_model="opus-5",
            stall_stage="wall_clock_exceeded",
            error="CDP generate no progress for max_wall_s=10",
        ),
        request_id="req-wall-c",
        thread_id="12286",
        to_agent="cursor-sdk-test",
        pointer_turn=2,
        via="worker",
    )
    confirmed = reconcile.read_inflight_leg("exec-wall-confirmed")
    assert confirmed is not None
    assert confirmed.proof_emitted is True


@pytest.mark.asyncio
async def test_reconcile_emits_proof_without_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[str] = []

    def _capture(factory: Any, **kwargs: Any) -> None:
        published.append(factory.__name__)

    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", _capture)
    monkeypatch.setattr(
        reconcile,
        "poll_satellite_snapshot",
        AsyncMock(return_value=_proof_snapshot()),
    )
    deliver_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        deliver_mock,
    )

    upsert_inflight_leg(
        execution_id="exec-reconcile-1",
        request_id="req-1",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    reconcile.attach_satellite_execution_id(
        execution_id="exec-reconcile-1",
        satellite_execution_id="sat-reconcile-1",
    )

    await reconcile.reconcile_cdp_inflight_legs()

    assert "CdpGenerateProof" in published
    leg = reconcile.read_inflight_leg("exec-reconcile-1")
    assert leg is not None
    assert leg.proof_emitted is True
    assert leg.delivered is True


@pytest.mark.asyncio
async def test_reconcile_running_satellite_no_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[str] = []
    monkeypatch.setattr(
        reconcile,
        "publish_cdp_kwargs",
        lambda factory, **kwargs: published.append(factory.__name__),
    )
    monkeypatch.setattr(
        reconcile,
        "poll_satellite_snapshot",
        AsyncMock(return_value=_running_snapshot()),
    )
    upsert_inflight_leg(
        execution_id="exec-running",
        request_id="req-1",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    reconcile.attach_satellite_execution_id(
        execution_id="exec-running",
        satellite_execution_id="sat-running",
    )
    await reconcile.reconcile_cdp_inflight_legs()
    assert published == []
    leg = reconcile.read_inflight_leg("exec-running")
    assert leg is not None
    assert leg.proof_emitted is False


@pytest.mark.asyncio
async def test_reconcile_poll_error_no_stalled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[str] = []
    monkeypatch.setattr(
        reconcile,
        "publish_cdp_kwargs",
        lambda factory, **kwargs: published.append(factory.__name__),
    )
    monkeypatch.setattr(
        reconcile,
        "poll_satellite_snapshot",
        AsyncMock(return_value={"error": "unreachable"}),
    )
    upsert_inflight_leg(
        execution_id="exec-poll-err",
        request_id="req-1",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    reconcile.attach_satellite_execution_id(
        execution_id="exec-poll-err",
        satellite_execution_id="sat-err",
    )
    await reconcile.reconcile_cdp_inflight_legs()
    assert published == []


@pytest.mark.asyncio
async def test_finalize_stalled_deliverable_present_from_archive_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stalled_kwargs: list[dict[str, Any]] = []

    def _capture(factory: Any, **kwargs: Any) -> None:
        if factory.__name__ == "CdpGenerateStalled":
            stalled_kwargs.append(kwargs)

    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", _capture)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        AsyncMock(return_value=True),
    )
    upsert_inflight_leg(
        execution_id="exec-stalled-deliv",
        request_id="req-1",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    archive = "cortex://notes/system/threads/cdp-ask-archive-new.md"
    result = CdpGenerateResult(
        ok=False,
        body="SKILLS_PROBE_OK",
        execution_id="exec-stalled-deliv",
        satellite_execution_id="sat-stalled",
        prompt_uri="cortex://p.md",
        picker_model="fable-5",
        archive_uri=archive,
        stall_stage="completed_without_proof",
        error="chat harvest lacks attested_model",
    )
    await finalize_cdp_generate(
        result=result,
        request_id="req-1",
        thread_id="5583",
        to_agent="cursor-sdk-test",
        pointer_turn=1,
        via="reconcile",
    )
    assert len(stalled_kwargs) == 1
    assert stalled_kwargs[0]["deliverable_present"] is True
    assert stalled_kwargs[0]["archive_uri"] == archive


@pytest.mark.asyncio
async def test_finalize_stalled_deliverable_present_from_extras_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stalled_kwargs: list[dict[str, Any]] = []

    def _capture(factory: Any, **kwargs: Any) -> None:
        if factory.__name__ == "CdpGenerateStalled":
            stalled_kwargs.append(kwargs)

    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", _capture)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        AsyncMock(return_value=True),
    )
    upsert_inflight_leg(
        execution_id="exec-stalled-extras",
        request_id="req-1",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    result = CdpGenerateResult(
        ok=False,
        body="partial harvest",
        execution_id="exec-stalled-extras",
        satellite_execution_id="sat-extras",
        prompt_uri="cortex://p.md",
        picker_model="fable-5",
        stall_stage="completed_without_proof",
        error="chat harvest lacks attested_model",
        extras={"deliverable_present_unproven": True},
    )
    await finalize_cdp_generate(
        result=result,
        request_id="req-1",
        thread_id="5583",
        to_agent="cursor-sdk-test",
        pointer_turn=1,
        via="reconcile",
    )
    assert len(stalled_kwargs) == 1
    assert stalled_kwargs[0]["deliverable_present"] is True


@pytest.mark.asyncio
async def test_finalize_idempotent_second_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish_count = 0

    def _capture(factory: Any, **kwargs: Any) -> None:
        nonlocal publish_count
        if factory.__name__ in {"CdpGenerateProof", "CdpGenerateStalled"}:
            publish_count += 1

    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", _capture)
    deliver = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        deliver,
    )
    upsert_inflight_leg(
        execution_id="exec-idem",
        request_id="req-1",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    result = CdpGenerateResult(
        ok=True,
        body="harvest",
        execution_id="exec-idem",
        satellite_execution_id="sat-idem",
        prompt_uri="cortex://p.md",
        picker_model="opus-5",
        archive_uri="cortex://a.md",
    )
    await finalize_cdp_generate(
        result=result,
        request_id="req-1",
        thread_id="5583",
        to_agent="cursor-sdk-test",
        pointer_turn=1,
        via="reconcile",
    )
    await finalize_cdp_generate(
        result=result,
        request_id="req-1",
        thread_id="5583",
        to_agent="cursor-sdk-test",
        pointer_turn=1,
        via="reconcile",
    )
    assert publish_count == 1
    assert deliver.await_count == 1
    leg = reconcile.read_inflight_leg("exec-idem")
    assert leg is not None
    assert leg.delivered is True


@pytest.mark.asyncio
async def test_delivery_fail_no_reproof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish_count = 0

    def _capture(factory: Any, **kwargs: Any) -> None:
        nonlocal publish_count
        if factory.__name__ == "CdpGenerateProof":
            publish_count += 1

    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", _capture)
    deliver = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        deliver,
    )
    upsert_inflight_leg(
        execution_id="exec-deliver-fail",
        request_id="req-1",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    result = CdpGenerateResult(
        ok=True,
        body="harvest",
        execution_id="exec-deliver-fail",
        satellite_execution_id="sat-fail",
        prompt_uri="cortex://p.md",
        picker_model="opus-5",
        archive_uri="cortex://a.md",
    )
    await finalize_cdp_generate(
        result=result,
        request_id="req-1",
        thread_id="5583",
        to_agent="cursor-sdk-test",
        pointer_turn=1,
        via="worker",
    )
    await finalize_cdp_generate(
        result=result,
        request_id="req-1",
        thread_id="5583",
        to_agent="cursor-sdk-test",
        pointer_turn=1,
        via="reconcile",
    )
    assert publish_count == 1
    leg = reconcile.read_inflight_leg("exec-deliver-fail")
    assert leg is not None
    assert leg.proof_emitted is True
    assert leg.delivered is False


def test_classify_horizon_probe() -> None:
    cse = "https://claude.ai/cowork/cse_horizon"
    assert reconcile.classify_horizon_probe(_running_snapshot()) == "alive"
    assert (
        reconcile.classify_horizon_probe({"status": "failed", "error": "boom"})
        == "confirmed_dead"
    )
    assert (
        reconcile.classify_horizon_probe(
            {"status": "failed", "error": "boom", "url": cse}
        )
        == "unverifiable"
    )
    assert (
        reconcile.classify_horizon_probe(
            {
                "status": "failed",
                "stall_stage": "weekly_limit",
                "error": "hit a limit",
            }
        )
        == "confirmed_dead"
    )
    assert (
        reconcile.classify_horizon_probe(
            {
                "status": "failed",
                "stall_stage": "weekly_limit",
                "error": "hit a limit",
                "url": cse,
            }
        )
        == "confirmed_dead"
    )
    assert (
        reconcile.classify_horizon_probe({"status": "aborted", "error": "aborted"})
        == "confirmed_dead"
    )
    assert (
        reconcile.classify_horizon_probe(
            {"status": "failed", "stall_stage": "observer_unverified"}
        )
        == "confirmed_dead"
    )
    assert reconcile.classify_horizon_probe({"error": "unreachable"}) == "unverifiable"
    assert reconcile.classify_horizon_probe(None) == "unverifiable"


@pytest.mark.asyncio
async def test_horizon_live_leg_not_abandoned(monkeypatch: pytest.MonkeyPatch) -> None:
    published: list[str] = []
    monkeypatch.setattr(
        reconcile,
        "publish_cdp_kwargs",
        lambda factory, **kwargs: published.append(factory.__name__),
    )
    monkeypatch.setattr(
        reconcile,
        "poll_satellite_snapshot",
        AsyncMock(return_value=_running_snapshot()),
    )
    upsert_inflight_leg(
        execution_id="exec-live-horizon",
        request_id="req-1",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    reconcile.attach_satellite_execution_id(
        execution_id="exec-live-horizon",
        satellite_execution_id="sat-live",
    )
    _age_leg_past_horizon("exec-live-horizon")

    await reconcile.reconcile_cdp_inflight_legs()

    assert published == []
    leg = reconcile.read_inflight_leg("exec-live-horizon")
    assert leg is not None
    assert leg.abandoned is False


@pytest.mark.asyncio
async def test_abandonment_emits_reconcile_abandoned_confirmed_dead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stalled: list[str | None] = []
    errors: list[str] = []

    def _capture(factory: Any, **kwargs: Any) -> None:
        if factory.__name__ == "CdpGenerateStalled":
            stalled.append(kwargs.get("stall_stage"))
            errors.append(str(kwargs.get("error") or ""))

    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", _capture)
    monkeypatch.setattr(
        reconcile,
        "poll_satellite_snapshot",
        AsyncMock(
            return_value={
                "status": "failed",
                "stall_stage": "weekly_limit",
                "error": "hit a limit",
            }
        ),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        AsyncMock(return_value=True),
    )
    upsert_inflight_leg(
        execution_id="exec-abandon",
        request_id="req-1",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    reconcile.attach_satellite_execution_id(
        execution_id="exec-abandon",
        satellite_execution_id="sat-dead",
    )
    _age_leg_past_horizon("exec-abandon")

    await reconcile.reconcile_cdp_inflight_legs()
    assert stalled == [reconcile.STALL_RECONCILE_ABANDONED_CONFIRMED]
    assert errors == ["satellite confirmed dead at horizon (status='failed')"]
    leg = reconcile.read_inflight_leg("exec-abandon")
    assert leg is not None
    assert leg.abandoned is True


@pytest.mark.asyncio
async def test_horizon_unreachable_probe_retains_unverifiable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[str] = []
    stalled: list[str | None] = []
    retained: list[dict[str, Any]] = []

    def _capture(factory: Any, **kwargs: Any) -> None:
        published.append(factory.__name__)
        if factory.__name__ == "CdpGenerateStalled":
            stalled.append(kwargs.get("stall_stage"))
        if factory.__name__ == "CdpGenerateHorizonUnverifiable":
            retained.append(kwargs)

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_events.publish_cdp_kwargs",
        _capture,
    )
    monkeypatch.setattr(
        reconcile,
        "poll_satellite_snapshot",
        AsyncMock(return_value={"error": "unreachable"}),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        AsyncMock(return_value=True),
    )
    upsert_inflight_leg(
        execution_id="exec-unreachable",
        request_id="req-1",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    reconcile.attach_satellite_execution_id(
        execution_id="exec-unreachable",
        satellite_execution_id="sat-unreachable",
    )
    _age_leg_past_horizon("exec-unreachable")

    await reconcile.reconcile_cdp_inflight_legs()
    assert published == ["CdpGenerateHorizonUnverifiable"]
    assert stalled == []
    assert reconcile.STALL_RECONCILE_ABANDONED_UNVERIFIABLE not in stalled
    assert retained == [
        {
            "request_id": "req-1",
            "execution_id": "exec-unreachable",
            "satellite_execution_id": "sat-unreachable",
            "thread_id": "5583",
            "stall_stage": reconcile.STALL_HORIZON_UNVERIFIABLE_RETAINED,
            "error": "unreachable",
        }
    ]
    leg = reconcile.read_inflight_leg("exec-unreachable")
    assert leg is not None
    assert leg.abandoned is False

    await reconcile.reconcile_cdp_inflight_legs()
    assert published == ["CdpGenerateHorizonUnverifiable"]


@pytest.mark.asyncio
async def test_horizon_404_plus_seated_authorship_not_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """9501-shaped: sat 404 + seated CSE turn on thread_id ⇒ not FAILED."""
    published: list[str] = []
    retained: list[dict[str, Any]] = []

    def _capture(factory: Any, **kwargs: Any) -> None:
        published.append(factory.__name__)
        if factory.__name__ == "CdpGenerateHorizonUnverifiable":
            retained.append(kwargs)

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_events.publish_cdp_kwargs",
        _capture,
    )
    monkeypatch.setattr(
        reconcile,
        "poll_satellite_snapshot",
        AsyncMock(return_value={"error": "cdp-ask HTTP 404", "status_code": 404}),
    )
    monkeypatch.setattr(
        reconcile,
        "seated_authorship_on_thread",
        AsyncMock(return_value=True),
    )
    upsert_inflight_leg(
        execution_id="a3ba868b-4aa9-4475-8944-1ac5981e48f6",
        request_id="req-9501",
        thread_id="9501",
        pointer_turn=10,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    reconcile.attach_satellite_execution_id(
        execution_id="a3ba868b-4aa9-4475-8944-1ac5981e48f6",
        satellite_execution_id="a8f51c9fc54e4d96bd591abebf053537",
    )
    _age_leg_past_horizon("a3ba868b-4aa9-4475-8944-1ac5981e48f6")

    await reconcile.reconcile_cdp_inflight_legs()
    assert published == ["CdpGenerateHorizonUnverifiable"]
    assert "CdpGenerateStalled" not in published
    assert retained == [
        {
            "request_id": "req-9501",
            "execution_id": "a3ba868b-4aa9-4475-8944-1ac5981e48f6",
            "satellite_execution_id": "a8f51c9fc54e4d96bd591abebf053537",
            "thread_id": "9501",
            "stall_stage": reconcile.STALL_HORIZON_SEATED_AUTHORSHIP,
            "error": "cdp-ask HTTP 404",
        }
    ]
    leg = reconcile.read_inflight_leg("a3ba868b-4aa9-4475-8944-1ac5981e48f6")
    assert leg is not None
    assert leg.abandoned is False
    assert leg.proof_emitted is False


@pytest.mark.asyncio
async def test_worker_cancelled_error_publishes_stalled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from systems.frontier_consult import cdp_generate_worker as worker

    finalize = AsyncMock()
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_reconcile.finalize_cdp_generate",
        finalize,
    )
    monkeypatch.setattr(
        worker.asyncio,
        "to_thread",
        AsyncMock(side_effect=asyncio.CancelledError()),
    )
    monkeypatch.setattr(
        worker,
        "publish_cdp_kwargs",
        MagicMock(),
    )

    with pytest.raises(asyncio.CancelledError):
        await worker.run_cdp_worker(
            execution_id="exec-cancel",
            model_id="cdp/opus-5",
            thread_id="5583",
            caller_agent="cursor-sdk-test",
            prompt_uri="cortex://p.md",
            request_id="req-1",
        )
    finalize.assert_awaited_once()
    call_result = finalize.await_args.kwargs["result"]
    assert call_result.stall_stage == "worker_cancelled"


@pytest.mark.asyncio
async def test_finalize_worker_reconcile_race_single_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-S1-e: forced worker+reconcile race yields exactly one bus delivery."""
    publish_count = 0

    def _capture(factory: Any, **kwargs: Any) -> None:
        nonlocal publish_count
        if factory.__name__ in {"CdpGenerateProof", "CdpGenerateStalled"}:
            publish_count += 1

    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", _capture)
    deliver = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        deliver,
    )
    upsert_inflight_leg(
        execution_id="exec-race",
        request_id="req-1",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    result = CdpGenerateResult(
        ok=True,
        body="harvest",
        execution_id="exec-race",
        satellite_execution_id="sat-race",
        prompt_uri="cortex://p.md",
        picker_model="opus-5",
        archive_uri="cortex://a.md",
        content_proof_uri="cortex://proof.md",
    )
    import asyncio

    await asyncio.gather(
        finalize_cdp_generate(
            result=result,
            request_id="req-1",
            thread_id="5583",
            to_agent="cursor-sdk-test",
            pointer_turn=1,
            via="worker",
        ),
        finalize_cdp_generate(
            result=result,
            request_id="req-1",
            thread_id="5583",
            to_agent="cursor-sdk-test",
            pointer_turn=1,
            via="reconcile",
        ),
    )
    assert publish_count == 1
    assert deliver.await_count == 1


@pytest.mark.asyncio
async def test_reconcile_module_has_no_run_cdp_generate() -> None:
    from pathlib import Path

    source = Path(reconcile.__file__).read_text(encoding="utf-8")
    assert "run_cdp_generate" not in source


@pytest.mark.asyncio
async def test_finalize_proof_carries_via_worker_and_reconcile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slice 1 — CdpGenerateProof publish receives via= from worker vs reconcile."""
    captured: list[dict[str, Any]] = []

    def _capture(factory: Any, **kwargs: Any) -> bool:
        if factory.__name__ == "CdpGenerateProof":
            captured.append(dict(kwargs))
        return True

    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", _capture)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        AsyncMock(return_value=True),
    )

    upsert_inflight_leg(
        execution_id="exec-via-worker",
        request_id="req-via",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    worker_result = CdpGenerateResult(
        ok=True,
        body="harvest",
        execution_id="exec-via-worker",
        satellite_execution_id="sat-via",
        prompt_uri="cortex://p.md",
        picker_model="opus-5",
        content_proof_uri="cortex://proof.md",
    )
    await finalize_cdp_generate(
        result=worker_result,
        request_id="req-via",
        thread_id="5583",
        to_agent="cursor-sdk-test",
        pointer_turn=1,
        via="worker",
    )

    upsert_inflight_leg(
        execution_id="exec-via-reconcile",
        request_id="req-via-2",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    reconcile_result = CdpGenerateResult(
        ok=True,
        body="harvest",
        execution_id="exec-via-reconcile",
        satellite_execution_id="sat-via",
        prompt_uri="cortex://p.md",
        picker_model="opus-5",
        content_proof_uri="cortex://proof.md",
    )
    await finalize_cdp_generate(
        result=reconcile_result,
        request_id="req-via-2",
        thread_id="5583",
        to_agent="cursor-sdk-test",
        pointer_turn=1,
        via="reconcile",
    )

    assert captured == [
        {
            "request_id": "req-via",
            "execution_id": "exec-via-worker",
            "satellite_execution_id": "sat-via",
            "archive_uri": None,
            "content_proof_uri": "cortex://proof.md",
            "via": "worker",
            "attested_by": None,
            "thread_id": "5583",
            "pointer_turn": 1,
            "dispatch_link_terminal": None,
        },
        {
            "request_id": "req-via-2",
            "execution_id": "exec-via-reconcile",
            "satellite_execution_id": "sat-via",
            "archive_uri": None,
            "content_proof_uri": "cortex://proof.md",
            "via": "reconcile",
            "attested_by": None,
            "thread_id": "5583",
            "pointer_turn": 1,
            "dispatch_link_terminal": None,
        },
    ]


@pytest.mark.asyncio
async def test_finalize_attest_does_not_emit_reconciled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attest success must not emit cdp.generate.reconciled."""
    published: list[str] = []

    def _capture(factory: Any, **kwargs: Any) -> bool:
        published.append(factory.__name__)
        return True

    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", _capture)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        AsyncMock(return_value=True),
    )
    upsert_inflight_leg(
        execution_id="exec-attest-no-reconcile",
        request_id="req-attest",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    result = CdpGenerateResult(
        ok=True,
        body="attested",
        execution_id="exec-attest-no-reconcile",
        satellite_execution_id="sat-attest",
        prompt_uri="cortex://p.md",
        picker_model="opus-5",
        content_proof_uri="cortex://proof.md",
        content_proof_sha256="abc",
    )
    await finalize_cdp_generate(
        result=result,
        request_id="req-attest",
        thread_id="5583",
        to_agent="cursor-sdk-test",
        pointer_turn=1,
        via="attest",
        attested_by="test-seat",
    )
    assert published == ["CdpGenerateProof"]


def _failed_result(*, execution_id: str = "exec-fail-order") -> CdpGenerateResult:
    return CdpGenerateResult(
        ok=False,
        body="",
        execution_id=execution_id,
        satellite_execution_id="sat-fail",
        prompt_uri="cortex://p.md",
        picker_model="fable-5",
        stall_stage="completed_without_proof",
        error="no proof",
    )


@pytest.mark.asyncio
async def test_finalize_failed_terminate_before_publish_and_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L1-AC-b: failed finalize terminates link before stalled publish and delivery."""
    order: list[str] = []

    record_cdp_admit(
        execution_id="exec-fail-order",
        thread_id="5583",
        pointer_turn=3,
        admit_reason="ok",
        caller_supplied_thread=True,
    )

    async def _terminate(**_kwargs: object) -> bool:
        order.append("terminate")
        return True

    def _publish(_factory: Any, **_kwargs: Any) -> bool:
        order.append("publish")
        return True

    async def _deliver(**_kwargs: object) -> bool:
        order.append("deliver")
        return True

    monkeypatch.setattr(
        "systems.frontier_consult.handoff.terminate_handoff_dispatch",
        _terminate,
    )
    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", _publish)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        _deliver,
    )
    upsert_inflight_leg(
        execution_id="exec-fail-order",
        request_id="req-fail-order",
        thread_id="5583",
        pointer_turn=3,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/fable",
        max_wall_s=1800.0,
    )
    await finalize_cdp_generate(
        result=_failed_result(),
        request_id="req-fail-order",
        thread_id="5583",
        to_agent="cursor-sdk-test",
        pointer_turn=3,
        via="worker",
    )
    assert order.index("terminate") < order.index("publish") < order.index("deliver")


@pytest.mark.asyncio
async def test_finalize_terminal_event_exists_terminate_before_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L1-AC-b: terminal_event_exists path terminates before on-behalf delivery."""
    order: list[str] = []

    record_cdp_admit(
        execution_id="exec-term-exists",
        thread_id="5583",
        pointer_turn=2,
        admit_reason="ok",
        caller_supplied_thread=True,
    )

    async def _terminate(**_kwargs: object) -> bool:
        order.append("terminate")
        return True

    def _publish(_factory: Any, **_kwargs: Any) -> bool:
        order.append("publish")

    async def _deliver(**_kwargs: object) -> bool:
        order.append("deliver")
        return True

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_reconcile.terminal_event_exists",
        lambda _eid: True,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.handoff.terminate_handoff_dispatch",
        _terminate,
    )
    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", _publish)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        _deliver,
    )
    upsert_inflight_leg(
        execution_id="exec-term-exists",
        request_id="req-term-exists",
        thread_id="5583",
        pointer_turn=2,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/fable",
        max_wall_s=1800.0,
    )
    await finalize_cdp_generate(
        result=_failed_result(execution_id="exec-term-exists"),
        request_id="req-term-exists",
        thread_id="5583",
        to_agent="cursor-sdk-test",
        pointer_turn=2,
        via="reconcile",
    )
    assert "terminate" in order
    assert "deliver" in order
    assert order.index("terminate") < order.index("deliver")
    assert "publish" not in order


@pytest.mark.asyncio
async def test_finalize_stalled_emits_join_key_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L1-AC-d: finalize stalled path passes join-key fields to CdpGenerateStalled."""
    stalled_kwargs: list[dict[str, Any]] = []

    record_cdp_admit(
        execution_id="exec-join-keys",
        thread_id="5583",
        pointer_turn=5,
        admit_reason="ok",
        caller_supplied_thread=True,
    )

    def _capture(factory: Any, **kwargs: Any) -> bool:
        if factory.__name__ == "CdpGenerateStalled":
            stalled_kwargs.append(dict(kwargs))
        return True

    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", _capture)
    monkeypatch.setattr(
        "systems.frontier_consult.handoff.terminate_handoff_dispatch",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        AsyncMock(return_value=True),
    )
    upsert_inflight_leg(
        execution_id="exec-join-keys",
        request_id="req-join-keys",
        thread_id="5583",
        pointer_turn=5,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/fable",
        max_wall_s=1800.0,
    )
    result = _failed_result(execution_id="exec-join-keys")
    result = CdpGenerateResult(
        ok=result.ok,
        body=result.body,
        execution_id=result.execution_id,
        satellite_execution_id=result.satellite_execution_id,
        prompt_uri=result.prompt_uri,
        picker_model=result.picker_model,
        stall_stage=result.stall_stage,
        error=result.error,
        extras={
            "registration_id": "reg-finalize",
            "chat_url": "https://claude.ai/cowork/cse_fin",
        },
    )
    await finalize_cdp_generate(
        result=result,
        request_id="req-join-keys",
        thread_id="5583",
        to_agent="cursor-sdk-test",
        pointer_turn=5,
        via="worker",
    )
    assert len(stalled_kwargs) == 1
    kw = stalled_kwargs[0]
    assert kw["thread_id"] == "5583"
    assert kw["pointer_turn"] == 5
    assert kw["dispatch_link_terminal"] is True
    assert kw["registration_id"] == "reg-finalize"
    assert kw["chat_url"] == "https://claude.ai/cowork/cse_fin"


def _ok_result(*, execution_id: str) -> CdpGenerateResult:
    return CdpGenerateResult(
        ok=True,
        body="done",
        execution_id=execution_id,
        satellite_execution_id="sat-ok",
        prompt_uri="cortex://p.md",
        picker_model="fable-5",
    )


@pytest.mark.asyncio
async def test_finalize_ok_terminal_event_terminates_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """result.ok on terminal_event_exists terminates completed before proof."""
    order: list[str] = []

    record_cdp_admit(
        execution_id="exec-ok-term",
        thread_id="5583",
        pointer_turn=2,
        admit_reason="ok",
        caller_supplied_thread=True,
    )

    async def _terminate(**kwargs: object) -> bool:
        order.append(f"terminate:{kwargs['terminal_status']}")
        return True

    real_mark = reconcile.mark_proof_emitted

    def _mark(execution_id: str) -> None:
        order.append("mark_proof")
        real_mark(execution_id)

    async def _deliver(**_kwargs: object) -> bool:
        order.append("deliver")
        return True

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_reconcile.terminal_event_exists",
        lambda _eid: True,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.handoff.terminate_handoff_dispatch",
        _terminate,
    )
    monkeypatch.setattr(reconcile, "mark_proof_emitted", _mark)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        _deliver,
    )
    upsert_inflight_leg(
        execution_id="exec-ok-term",
        request_id="req-ok-term",
        thread_id="5583",
        pointer_turn=2,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/fable",
        max_wall_s=1800.0,
    )
    await finalize_cdp_generate(
        result=_ok_result(execution_id="exec-ok-term"),
        request_id="req-ok-term",
        thread_id="5583",
        to_agent="cursor-sdk-test",
        pointer_turn=2,
        via="worker",
    )
    assert order.index("terminate:completed") < order.index("mark_proof")
    assert "deliver" in order


@pytest.mark.asyncio
async def test_case_l_live_leg_stays_null(monkeypatch: pytest.MonkeyPatch) -> None:
    """Horizon with no satellite id and no terminal event does not finalize."""
    execution_id = "662daf5d-c192-46fa-bec3-066aa4284f1a"
    finalized: list[str] = []
    terminated: list[dict[str, Any]] = []

    async def _finalize(**kwargs: object) -> None:
        finalized.append(str(kwargs.get("result")))

    async def _terminate(**kwargs: object) -> bool:
        terminated.append(dict(kwargs))
        return True

    monkeypatch.setattr(reconcile, "finalize_cdp_generate", _finalize)
    monkeypatch.setattr(
        "systems.frontier_consult.handoff.terminate_handoff_dispatch",
        _terminate,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_events.publish_cdp_kwargs",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(reconcile, "terminal_event_exists", lambda _eid: False)
    upsert_inflight_leg(
        execution_id=execution_id,
        request_id="req-case-l",
        thread_id="12286",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    _age_leg_past_horizon(execution_id)
    leg = reconcile.read_inflight_leg(execution_id)
    assert leg is not None
    assert not leg.satellite_execution_id
    await reconcile._reconcile_horizon_leg(
        leg, horizon=max_open_leg_s(1800.0)
    )
    assert finalized == []
    assert terminated == []


def _relay_finalize_mocks(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    relay_calls: list[dict[str, Any]] = []

    async def _relay(**kwargs: object) -> dict[str, str]:
        relay_calls.append(dict(kwargs))
        return {"ok": True}

    monkeypatch.setattr(
        "systems.frontier_consult.work_registry_admit_relay.relay_registry_terminal",
        _relay,
    )
    monkeypatch.setattr(reconcile, "terminal_event_exists", lambda _eid: False)
    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "systems.frontier_consult.handoff.terminate_handoff_dispatch",
        AsyncMock(return_value=True),
    )
    return relay_calls


@pytest.mark.asyncio
async def test_finalize_ok_posted_relays_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay_calls = _relay_finalize_mocks(monkeypatch)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        AsyncMock(return_value=True),
    )
    upsert_inflight_leg(
        execution_id="exec-relay-ok",
        request_id="req-relay-ok",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    await finalize_cdp_generate(
        result=_ok_result(execution_id="exec-relay-ok"),
        request_id="req-relay-ok",
        thread_id="5583",
        to_agent="cursor-sdk-test",
        pointer_turn=1,
        via="worker",
    )
    assert relay_calls[-1]["terminal_status"] == "completed"
    assert relay_calls[-1]["dispatch_id"] == "exec-relay-ok"


@pytest.mark.asyncio
async def test_finalize_not_ok_relays_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    relay_calls = _relay_finalize_mocks(monkeypatch)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        AsyncMock(return_value=True),
    )
    upsert_inflight_leg(
        execution_id="exec-relay-fail",
        request_id="req-relay-fail",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    await finalize_cdp_generate(
        result=CdpGenerateResult(
            ok=False,
            body="",
            execution_id="exec-relay-fail",
            satellite_execution_id="sat-fail",
            prompt_uri="cortex://p.md",
            picker_model="opus-5",
            stall_stage="submit_failed",
            error="submit failed",
        ),
        request_id="req-relay-fail",
        thread_id="5583",
        to_agent="cursor-sdk-test",
        pointer_turn=1,
        via="worker",
    )
    assert relay_calls[-1]["terminal_status"] == "failed"


@pytest.mark.asyncio
async def test_finalize_ok_post_failed_relays_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay_calls = _relay_finalize_mocks(monkeypatch)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        AsyncMock(return_value=False),
    )
    upsert_inflight_leg(
        execution_id="exec-relay-post-fail",
        request_id="req-relay-post-fail",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    await finalize_cdp_generate(
        result=_ok_result(execution_id="exec-relay-post-fail"),
        request_id="req-relay-post-fail",
        thread_id="5583",
        to_agent="cursor-sdk-test",
        pointer_turn=1,
        via="worker",
    )
    assert relay_calls[-1]["terminal_status"] == "failed"


@pytest.mark.asyncio
async def test_finalize_unconfirmed_wall_stall_does_not_relay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cdp_ask.unverifiable import WALL_CLOCK_EXCEEDED_ABORT_UNCONFIRMED

    relay_calls = _relay_finalize_mocks(monkeypatch)
    upsert_inflight_leg(
        execution_id="exec-relay-wall-u",
        request_id="req-relay-wall-u",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    await finalize_cdp_generate(
        result=CdpGenerateResult(
            ok=False,
            body="",
            execution_id="exec-relay-wall-u",
            satellite_execution_id="sat-wall-u",
            prompt_uri="cortex://p.md",
            picker_model="opus-5",
            stall_stage=WALL_CLOCK_EXCEEDED_ABORT_UNCONFIRMED,
            error="stall",
        ),
        request_id="req-relay-wall-u",
        thread_id="5583",
        to_agent="cursor-sdk-test",
        pointer_turn=1,
        via="worker",
    )
    assert relay_calls == []


@pytest.mark.asyncio
async def test_reconcile_finalize_relays_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay_calls = _relay_finalize_mocks(monkeypatch)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        AsyncMock(return_value=True),
    )
    upsert_inflight_leg(
        execution_id="exec-relay-reconcile",
        request_id="req-relay-reconcile",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    await finalize_cdp_generate(
        result=_ok_result(execution_id="exec-relay-reconcile"),
        request_id="req-relay-reconcile",
        thread_id="5583",
        to_agent="cursor-sdk-test",
        pointer_turn=1,
        via="reconcile",
    )
    assert any(c["terminal_status"] == "completed" for c in relay_calls)


@pytest.mark.asyncio
async def test_reconcile_alive_before_horizon_relays_registry_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Running satellite + open leg: heartbeat relay, then reaper respects bump."""
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )
    from services.git_integration_worker.cursor_sdk_work_registry_admit import (
        bump_frontier_registry_heartbeat,
        reap_stale_frontier_registry_rows,
        upsert_frontier_registry_dispatch,
    )

    db = tmp_path / "giw.sqlite"
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_LEDGER", str(db))
    CursorDispatchLedger._instance = None
    ledger = CursorDispatchLedger.instance()

    execution_id = "exec-hb-reconcile"
    five_h_ago = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    upsert_frontier_registry_dispatch(
        dispatch_id=execution_id,
        thread_id="21835",
        caller_agent="cursor-sdk-test",
        seat_name="dispatch",
        address={"kind": "cdp", "thread_id": "21835"},
        work_key="todo:hb-reconcile",
        max_wall_s=43200,
        ledger=ledger,
    )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET started_at=?, queued_at=? "
            "WHERE dispatch_id=?",
            (five_h_ago, five_h_ago, execution_id),
        )
        conn.commit()

    hb_calls: list[str] = []

    async def _relay_hb(**kwargs: object) -> dict[str, bool]:
        hb_calls.append(str(kwargs.get("dispatch_id")))
        bump_frontier_registry_heartbeat(str(kwargs.get("dispatch_id")), ledger=ledger)
        return {"ok": True, "bumped": True}

    monkeypatch.setattr(
        "systems.frontier_consult.work_registry_admit_relay.relay_registry_heartbeat",
        _relay_hb,
    )
    monkeypatch.setattr(
        reconcile,
        "poll_satellite_snapshot",
        AsyncMock(return_value=_running_snapshot()),
    )
    monkeypatch.setenv("CDP_REGISTRY_HEARTBEAT_MIN_S", "0")
    reconcile._last_registry_heartbeat_mono.clear()

    upsert_inflight_leg(
        execution_id=execution_id,
        request_id="req-hb",
        thread_id="21835",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=43200.0,
    )
    reconcile.attach_satellite_execution_id(
        execution_id=execution_id,
        satellite_execution_id="sat-hb",
    )
    admitted = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    conn = _connect()
    try:
        conn.execute(
            "UPDATE cdp_inflight_leg SET admitted_at=? WHERE execution_id=?",
            (admitted, execution_id),
        )
        conn.commit()
    finally:
        conn.close()

    await reconcile._reconcile_leg(reconcile.read_inflight_leg(execution_id))  # type: ignore[arg-type]
    await asyncio.sleep(0)

    assert hb_calls == [execution_id]
    reaped = reap_stale_frontier_registry_rows(ledger=ledger, ttl_s=14400)
    assert execution_id not in reaped


@pytest.mark.asyncio
async def test_horizon_unverifiable_no_registry_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hb_calls: list[str] = []

    async def _relay_hb(**kwargs: object) -> dict[str, bool]:
        hb_calls.append(str(kwargs.get("dispatch_id")))
        return {"ok": True}

    monkeypatch.setattr(
        "systems.frontier_consult.work_registry_admit_relay.relay_registry_heartbeat",
        _relay_hb,
    )
    monkeypatch.setenv("CDP_REGISTRY_HEARTBEAT_MIN_S", "0")
    reconcile._last_registry_heartbeat_mono.clear()

    upsert_inflight_leg(
        execution_id="exec-no-sat-hb",
        request_id="req-no-sat",
        thread_id="21835",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    _age_leg_past_horizon("exec-no-sat-hb")
    await reconcile._reconcile_horizon_leg(
        reconcile.read_inflight_leg("exec-no-sat-hb"),  # type: ignore[arg-type]
        horizon=max_open_leg_s(1800.0),
    )
    assert hb_calls == []


def test_cdp_registry_row_null_heartbeat_reaped_after_margin(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )
    from services.git_integration_worker.cursor_sdk_work_registry_admit import (
        reap_stale_frontier_registry_rows,
        upsert_frontier_registry_dispatch,
    )

    monkeypatch.setenv("CURSOR_REGISTRY_FRONTIER_WALL_MARGIN_S", "900")
    db = tmp_path / "giw2.sqlite"
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_LEDGER", str(db))
    CursorDispatchLedger._instance = None
    ledger = CursorDispatchLedger.instance()
    started = (datetime.now(UTC) - timedelta(hours=4, minutes=1)).isoformat()
    upsert_frontier_registry_dispatch(
        dispatch_id="exec-null-hb-reap",
        thread_id="21835",
        caller_agent="cursor-sdk-test",
        seat_name="dispatch",
        address={"kind": "cdp", "thread_id": "21835"},
        work_key="todo:null-hb",
        max_wall_s=1800,
        ledger=ledger,
    )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET started_at=?, queued_at=?, "
            "last_heartbeat_at=NULL WHERE dispatch_id=?",
            (started, started, "exec-null-hb-reap"),
        )
        conn.commit()
    reaped = reap_stale_frontier_registry_rows(ledger=ledger, ttl_s=14400)
    assert reaped == ["exec-null-hb-reap"]


@pytest.mark.asyncio
async def test_registry_heartbeat_throttled_two_ticks_one_relay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hb_calls: list[str] = []
    mono = [1000.0]

    async def _relay_hb(**kwargs: object) -> dict[str, bool]:
        hb_calls.append(str(kwargs.get("dispatch_id")))
        return {"ok": True}

    monkeypatch.setattr(
        "systems.frontier_consult.work_registry_admit_relay.relay_registry_heartbeat",
        _relay_hb,
    )
    monkeypatch.setattr(reconcile.time, "monotonic", lambda: mono[0])
    monkeypatch.setenv("CDP_REGISTRY_HEARTBEAT_MIN_S", "300")
    reconcile._last_registry_heartbeat_mono.clear()

    monkeypatch.setattr(
        reconcile,
        "poll_satellite_snapshot",
        AsyncMock(return_value=_running_snapshot()),
    )
    upsert_inflight_leg(
        execution_id="exec-throttle-hb",
        request_id="req-throttle",
        thread_id="21835",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=43200.0,
    )
    reconcile.attach_satellite_execution_id(
        execution_id="exec-throttle-hb",
        satellite_execution_id="sat-throttle",
    )
    leg = reconcile.read_inflight_leg("exec-throttle-hb")
    assert leg is not None
    await reconcile._reconcile_leg(leg)
    await asyncio.sleep(0)
    mono[0] += 20.0
    await reconcile._reconcile_leg(leg)
    await asyncio.sleep(0)
    assert hb_calls == ["exec-throttle-hb"]


@pytest.mark.asyncio
async def test_registry_heartbeat_mono_pruned_after_leg_finalizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay_calls = _relay_finalize_mocks(monkeypatch)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        AsyncMock(return_value=True),
    )
    execution_id = "exec-hb-prune-finalize"
    reconcile._last_registry_heartbeat_mono[execution_id] = 1000.0
    reconcile._last_registry_heartbeat_mono["exec-stale-closed"] = 2000.0
    upsert_inflight_leg(
        execution_id=execution_id,
        request_id="req-hb-prune",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    await finalize_cdp_generate(
        result=_ok_result(execution_id=execution_id),
        request_id="req-hb-prune",
        thread_id="5583",
        to_agent="cursor-sdk-test",
        pointer_turn=1,
        via="reconcile",
    )
    await reconcile.reconcile_cdp_inflight_legs()
    assert execution_id not in reconcile._last_registry_heartbeat_mono
    assert "exec-stale-closed" not in reconcile._last_registry_heartbeat_mono
    assert relay_calls  # finalize path exercised


@pytest.mark.asyncio
async def test_registry_heartbeat_mono_pruned_after_abandon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _relay_finalize_mocks(monkeypatch)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        AsyncMock(return_value=True),
    )
    execution_id = "exec-hb-prune-abandon"
    reconcile._last_registry_heartbeat_mono[execution_id] = 1000.0
    upsert_inflight_leg(
        execution_id=execution_id,
        request_id="req-hb-abandon",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    reconcile.attach_satellite_execution_id(
        execution_id=execution_id,
        satellite_execution_id="sat-hb-abandon",
    )
    _age_leg_past_horizon(execution_id)
    monkeypatch.setattr(
        reconcile,
        "poll_satellite_snapshot",
        AsyncMock(
            return_value={
                "status": "failed",
                "stall_stage": "weekly_limit",
                "error": "hit a limit",
            }
        ),
    )
    await reconcile.reconcile_cdp_inflight_legs()
    assert execution_id not in reconcile._last_registry_heartbeat_mono
    leg = reconcile.read_inflight_leg(execution_id)
    assert leg is not None
    assert leg.abandoned is True


@pytest.mark.asyncio
async def test_reconcile_tick_finishes_while_registry_heartbeat_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hang = asyncio.Event()

    async def _hang(**kwargs: object) -> None:
        await hang.wait()

    monkeypatch.setattr(
        "systems.frontier_consult.work_registry_admit_relay.relay_registry_heartbeat",
        _hang,
    )
    monkeypatch.setenv("CDP_REGISTRY_HEARTBEAT_MIN_S", "0")
    monkeypatch.setattr(
        reconcile,
        "poll_satellite_snapshot",
        AsyncMock(return_value=_running_snapshot()),
    )
    reconcile._last_registry_heartbeat_mono.clear()
    upsert_inflight_leg(
        execution_id="exec-hb-nonblock",
        request_id="req-hb-nonblock",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=43200.0,
    )
    reconcile.attach_satellite_execution_id(
        execution_id="exec-hb-nonblock",
        satellite_execution_id="sat-nonblock",
    )
    started = time.monotonic()
    await asyncio.wait_for(reconcile.reconcile_cdp_inflight_legs(), timeout=0.5)
    elapsed = time.monotonic() - started
    assert elapsed < 0.25


def _upsert_open_leg_for_hb(
    execution_id: str,
    *,
    request_id: str | None = None,
) -> reconcile.InflightLeg:
    upsert_inflight_leg(
        execution_id=execution_id,
        request_id=request_id or f"req-{execution_id}",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=43200.0,
    )
    reconcile.attach_satellite_execution_id(
        execution_id=execution_id,
        satellite_execution_id=f"sat-{execution_id}",
    )
    leg = reconcile.read_inflight_leg(execution_id)
    assert leg is not None
    return leg


@pytest.mark.asyncio
async def test_registry_heartbeat_create_task_failure_clears_inflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution_id = "exec-hb-create-task-fail"
    real_create_task = asyncio.create_task

    def _create_task_raises(coro, **kwargs: object):  # type: ignore[no-untyped-def]
        raise OSError("create_task rejected")

    monkeypatch.setattr(asyncio, "create_task", _create_task_raises)
    monkeypatch.setenv("CDP_REGISTRY_HEARTBEAT_MIN_S", "0")
    reconcile._last_registry_heartbeat_mono.clear()
    reconcile._REGISTRY_HB_INFLIGHT.clear()
    leg = _upsert_open_leg_for_hb(execution_id)

    with pytest.raises(OSError, match="create_task rejected"):
        await reconcile._maybe_relay_registry_heartbeat(leg)

    assert execution_id not in reconcile._REGISTRY_HB_INFLIGHT
    monkeypatch.setattr(asyncio, "create_task", real_create_task)


@pytest.mark.asyncio
async def test_registry_heartbeat_relay_exception_logged_not_unretrieved(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    execution_id = "exec-hb-relay-err"

    async def _boom(**kwargs: object) -> None:
        raise RuntimeError("giw down")

    monkeypatch.setattr(
        "systems.frontier_consult.work_registry_admit_relay.relay_registry_heartbeat",
        _boom,
    )
    monkeypatch.setenv("CDP_REGISTRY_HEARTBEAT_MIN_S", "0")
    reconcile._last_registry_heartbeat_mono.clear()
    leg = _upsert_open_leg_for_hb(execution_id)

    caplog.set_level(logging.WARNING)
    await reconcile._maybe_relay_registry_heartbeat(leg)
    await asyncio.sleep(0.05)

    warnings = [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING and execution_id in r.getMessage()
    ]
    assert len(warnings) == 1
    assert reconcile._REGISTRY_HB_TASKS == set()
    assert reconcile._REGISTRY_HB_INFLIGHT == set()
    assert "Task exception was never retrieved" not in caplog.text


@pytest.mark.asyncio
async def test_registry_heartbeat_single_flight_while_relay_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hang = asyncio.Event()
    hb_calls: list[str] = []

    async def _relay_hb(**kwargs: object) -> None:
        hb_calls.append(str(kwargs.get("dispatch_id")))
        await hang.wait()

    monkeypatch.setattr(
        "systems.frontier_consult.work_registry_admit_relay.relay_registry_heartbeat",
        _relay_hb,
    )
    monkeypatch.setenv("CDP_REGISTRY_HEARTBEAT_MIN_S", "0")
    reconcile._last_registry_heartbeat_mono.clear()
    leg = _upsert_open_leg_for_hb("exec-hb-single-flight")

    for _ in range(3):
        await reconcile._maybe_relay_registry_heartbeat(leg)
    await asyncio.sleep(0.05)
    assert hb_calls == ["exec-hb-single-flight"]

    hang.set()
    for _ in range(20):
        if not reconcile._REGISTRY_HB_INFLIGHT:
            break
        await asyncio.sleep(0.02)
    assert reconcile._REGISTRY_HB_TASKS == set()

    await reconcile._maybe_relay_registry_heartbeat(leg)
    await asyncio.sleep(0.05)
    assert hb_calls == ["exec-hb-single-flight", "exec-hb-single-flight"]


class _InflightGuardDisabled(set[str]):
    """Simulates pre-C2 pile-up: ids never appear in-flight."""

    def add(self, item: str) -> None:
        return

    def __contains__(self, item: object) -> bool:
        return False


@pytest.mark.asyncio
async def test_registry_heartbeat_single_flight_guard_prevents_pile_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hang = asyncio.Event()
    hb_calls: list[str] = []

    async def _relay_hb(**kwargs: object) -> None:
        hb_calls.append(str(kwargs.get("dispatch_id")))
        await hang.wait()

    monkeypatch.setattr(
        "systems.frontier_consult.work_registry_admit_relay.relay_registry_heartbeat",
        _relay_hb,
    )
    monkeypatch.setattr(reconcile, "_REGISTRY_HB_INFLIGHT", _InflightGuardDisabled())
    monkeypatch.setenv("CDP_REGISTRY_HEARTBEAT_MIN_S", "0")
    reconcile._last_registry_heartbeat_mono.clear()
    leg = _upsert_open_leg_for_hb("exec-hb-no-guard")

    for _ in range(3):
        await reconcile._maybe_relay_registry_heartbeat(leg)
    await asyncio.sleep(0.05)
    assert len(hb_calls) == 3

    hang.set()
    for _ in range(50):
        if not reconcile._REGISTRY_HB_TASKS:
            break
        await asyncio.sleep(0.02)
    assert reconcile._REGISTRY_HB_TASKS == set()
    assert reconcile._REGISTRY_HB_INFLIGHT == set()


@pytest.mark.asyncio
async def test_registry_heartbeat_single_flight_different_legs_parallel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hang_a = asyncio.Event()
    hang_b = asyncio.Event()
    hb_calls: list[str] = []

    async def _relay_hb(**kwargs: object) -> None:
        hb_calls.append(str(kwargs.get("dispatch_id")))
        dispatch_id = str(kwargs.get("dispatch_id"))
        if dispatch_id == "exec-hb-leg-a":
            await hang_a.wait()
        else:
            await hang_b.wait()

    monkeypatch.setattr(
        "systems.frontier_consult.work_registry_admit_relay.relay_registry_heartbeat",
        _relay_hb,
    )
    monkeypatch.setenv("CDP_REGISTRY_HEARTBEAT_MIN_S", "0")
    reconcile._last_registry_heartbeat_mono.clear()
    leg_a = _upsert_open_leg_for_hb("exec-hb-leg-a")
    leg_b = _upsert_open_leg_for_hb("exec-hb-leg-b")

    await reconcile._maybe_relay_registry_heartbeat(leg_a)
    await reconcile._maybe_relay_registry_heartbeat(leg_b)
    await asyncio.sleep(0.05)
    assert set(hb_calls) == {"exec-hb-leg-a", "exec-hb-leg-b"}
    assert len(reconcile._REGISTRY_HB_INFLIGHT) == 2

    hang_a.set()
    hang_b.set()
    for _ in range(50):
        if not reconcile._REGISTRY_HB_TASKS:
            break
        await asyncio.sleep(0.02)
    assert reconcile._REGISTRY_HB_TASKS == set()
    assert reconcile._REGISTRY_HB_INFLIGHT == set()


@pytest.mark.asyncio
async def test_reconcile_leg_without_caller_agent_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing mailbox caller fails closed before terminate/relay delivery."""
    stalled: list[dict[str, Any]] = []
    relay_calls = _relay_finalize_mocks(monkeypatch)

    def _capture_stall(event_cls: type, **kwargs: object) -> None:
        stalled.append({"event": event_cls.__name__, **kwargs})

    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", _capture_stall)
    upsert_inflight_leg(
        execution_id="exec-no-caller",
        request_id="req-no-caller",
        thread_id="99001",
        pointer_turn=1,
        caller_agent="",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    await finalize_cdp_generate(
        result=_ok_result(execution_id="exec-no-caller"),
        request_id="req-no-caller",
        thread_id="99001",
        to_agent="",
        pointer_turn=1,
        via="reconcile",
    )
    assert relay_calls
    assert relay_calls[-1]["terminal_status"] == "failed"
    assert relay_calls[-1]["reason"] == "cdp_caller_agent_missing"
    assert any(
        s.get("stall_stage") == "cdp_caller_agent_missing" for s in stalled
    )
    leg = reconcile.read_inflight_leg("exec-no-caller")
    assert leg is not None
    assert leg.proof_emitted


@pytest.mark.asyncio
async def test_cdp_terminal_stores_harvest_chars_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second finalize after proof_emitted does not relay harvest again."""
    relay_calls = _relay_finalize_mocks(monkeypatch)
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_worker.deliver_cdp_result_turn",
        AsyncMock(return_value=True),
    )
    upsert_inflight_leg(
        execution_id="exec-harvest-once",
        request_id="req-harvest-once",
        thread_id="99002",
        pointer_turn=1,
        caller_agent="cursor-sdk-test",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    result = _ok_result(execution_id="exec-harvest-once")
    await finalize_cdp_generate(
        result=result,
        request_id="req-harvest-once",
        thread_id="99002",
        to_agent="cursor-sdk-test",
        pointer_turn=1,
        via="worker",
    )
    await finalize_cdp_generate(
        result=result,
        request_id="req-harvest-once",
        thread_id="99002",
        to_agent="cursor-sdk-test",
        pointer_turn=1,
        via="worker",
    )
    assert len(relay_calls) == 1
    assert relay_calls[0]["harvest_chars"] == len(str(result.body or ""))
