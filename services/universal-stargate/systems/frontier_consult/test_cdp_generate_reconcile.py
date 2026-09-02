"""Regression tests for CDP generate reconcile + finalize idempotency."""

from __future__ import annotations

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
        caller_agent="dispatch",
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
        caller_agent="dispatch",
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
        caller_agent="dispatch",
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
        caller_agent="dispatch",
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
        to_agent="dispatch",
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
        caller_agent="dispatch",
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
        to_agent="dispatch",
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
        caller_agent="dispatch",
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
        to_agent="dispatch",
        pointer_turn=1,
        via="reconcile",
    )
    await finalize_cdp_generate(
        result=result,
        request_id="req-1",
        thread_id="5583",
        to_agent="dispatch",
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
        caller_agent="dispatch",
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
        to_agent="dispatch",
        pointer_turn=1,
        via="worker",
    )
    await finalize_cdp_generate(
        result=result,
        request_id="req-1",
        thread_id="5583",
        to_agent="dispatch",
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
        caller_agent="dispatch",
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
        caller_agent="dispatch",
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
        caller_agent="dispatch",
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
        caller_agent="dispatch",
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
            caller_agent="dispatch",
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
        caller_agent="dispatch",
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
            to_agent="dispatch",
            pointer_turn=1,
            via="worker",
        ),
        finalize_cdp_generate(
            result=result,
            request_id="req-1",
            thread_id="5583",
            to_agent="dispatch",
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
        caller_agent="dispatch",
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
        to_agent="dispatch",
        pointer_turn=1,
        via="worker",
    )

    upsert_inflight_leg(
        execution_id="exec-via-reconcile",
        request_id="req-via-2",
        thread_id="5583",
        pointer_turn=1,
        caller_agent="dispatch",
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
        to_agent="dispatch",
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
        },
        {
            "request_id": "req-via-2",
            "execution_id": "exec-via-reconcile",
            "satellite_execution_id": "sat-via",
            "archive_uri": None,
            "content_proof_uri": "cortex://proof.md",
            "via": "reconcile",
            "attested_by": None,
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
        caller_agent="dispatch",
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
        to_agent="dispatch",
        pointer_turn=1,
        via="attest",
        attested_by="test-seat",
    )
    assert published == ["CdpGenerateProof"]
