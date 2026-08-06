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
    assert reconcile.classify_horizon_probe(_running_snapshot()) == "alive"
    assert (
        reconcile.classify_horizon_probe({"status": "failed", "error": "boom"})
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
    old = (
        datetime.now(UTC) - timedelta(seconds=max_open_leg_s(1800.0) + 10)
    ).isoformat()
    conn = _connect()
    try:
        conn.execute(
            "UPDATE cdp_inflight_leg SET admitted_at=? WHERE execution_id=?",
            (old, "exec-live-horizon"),
        )
        conn.commit()
    finally:
        conn.close()

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
        AsyncMock(return_value={"status": "failed", "error": "satellite dead"}),
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
    old = (
        datetime.now(UTC) - timedelta(seconds=max_open_leg_s(1800.0) + 10)
    ).isoformat()
    conn = _connect()
    try:
        conn.execute(
            "UPDATE cdp_inflight_leg SET admitted_at=? WHERE execution_id=?",
            (old, "exec-abandon"),
        )
        conn.commit()
    finally:
        conn.close()

    await reconcile.reconcile_cdp_inflight_legs()
    assert stalled == [reconcile.STALL_RECONCILE_ABANDONED_CONFIRMED]
    assert errors == ["satellite confirmed dead at horizon (status='failed')"]
    leg = reconcile.read_inflight_leg("exec-abandon")
    assert leg is not None
    assert leg.abandoned is True


@pytest.mark.asyncio
async def test_horizon_unreachable_probe_abandons_unverifiable(
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
    old = (
        datetime.now(UTC) - timedelta(seconds=max_open_leg_s(1800.0) + 10)
    ).isoformat()
    conn = _connect()
    try:
        conn.execute(
            "UPDATE cdp_inflight_leg SET admitted_at=? WHERE execution_id=?",
            (old, "exec-unreachable"),
        )
        conn.commit()
    finally:
        conn.close()

    await reconcile.reconcile_cdp_inflight_legs()
    assert stalled == [reconcile.STALL_RECONCILE_ABANDONED_UNVERIFIABLE]
    assert errors == ["horizon crossed; liveness unverifiable: unreachable"]
    leg = reconcile.read_inflight_leg("exec-unreachable")
    assert leg is not None
    assert leg.abandoned is True


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
