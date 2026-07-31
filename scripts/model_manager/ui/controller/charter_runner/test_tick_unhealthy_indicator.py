"""Falsifiers F1–F6 for charter tick unhealthy indicator (BIND 6249)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from scripts.model_manager.ui.controller.charter_runner import ledger_age, root_health
from scripts.model_manager.ui.controller.charter_runner.admission.caps import CapStore
from scripts.model_manager.ui.controller.charter_runner.root_health import (
    FireAttemptOutcome,
    compute_unhealthy,
    is_declared_wait,
    observe_root_health,
    root_has_demand,
)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path / "runner-data"))
    monkeypatch.setenv("CHARTER_GATE_DEFER_MAX_AGE_S", "2700")
    return tmp_path / "runner-data"


@pytest.mark.offline
def test_f1_classified_admit_failure_carries_outcome() -> None:
    """F1 — dispatch branch map yields non-null FireAttemptOutcome."""
    assert (
        root_health.map_dispatch_failure("pointer_post_failed")
        == FireAttemptOutcome.FIRED_BOOKKEEPING_FAILED
    )
    assert (
        root_health.map_dispatch_failure("admission_rejected")
        == FireAttemptOutcome.REFUSED_PRE_FIRE
    )
    assert (
        root_health.map_dispatch_failure("admission_transport_error")
        == FireAttemptOutcome.ERRORED_PRE_FIRE
    )
    assert (
        root_health.map_dispatch_failure("gate_defer")
        == FireAttemptOutcome.DEFERRED_LEGAL
    )
    assert (
        root_health.map_dispatch_failure("unknown_branch")
        == FireAttemptOutcome.ERRORED_PRE_FIRE
    )


@pytest.mark.offline
def test_f2_episode_claim_root_id_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F2 — ≤1 concurrent mission per episode (claim key root_id alone)."""
    from pager_notify import sos as pager_sos

    monkeypatch.setenv("PAGER_NOTIFY_STATE_DIR", str(tmp_path / "pager"))
    assert pager_sos.claim_tick_sos("6237", "sticky_admitted", now=100.0) is True
    assert pager_sos.claim_tick_sos("6237", "executor_mismatch", now=200.0) is False
    assert pager_sos.claim_tick_sos("6238", "executor_mismatch", now=200.0) is True


@pytest.mark.offline
def test_f3_cap_stop_survives_recycle(data_dir: Path) -> None:
    """F3 — stopped_reason durable across CapStore recycle."""
    caps = CapStore()
    caps.mark_failed("6237", "admission_transport_error")
    stop_path = data_dir / "cap-stops" / "6237.json"
    assert stop_path.is_file()

    recycled = CapStore()
    allowed, reason = recycled.check("6237")
    assert allowed is False
    assert reason == "stopped:admission_transport_error"

    recycled.reset("6237")
    allowed, reason = recycled.check("6237")
    assert allowed is True
    assert reason is None
    assert not stop_path.is_file()


@pytest.mark.offline
def test_f4_declared_wait_dormant_not_unhealthy(data_dir: Path) -> None:
    """F4 — declared-wait / dormant roots do not fast-leg mark."""
    assert is_declared_wait(
        FireAttemptOutcome.NO_ATTEMPT_QUIET,
        skipped_reason="dormant",
    )
    assert not compute_unhealthy(
        "6237",
        FireAttemptOutcome.NO_ATTEMPT_QUIET,
        skipped_reason="dormant",
        data_dir=data_dir,
    )
    assert not root_has_demand(
        FireAttemptOutcome.NO_ATTEMPT_QUIET,
        skipped_reason="empty_hopper",
        consult_pending=False,
    )


@pytest.mark.offline
def test_f5_wait_classes_never_fast_unhealthy(data_dir: Path) -> None:
    """F5 — deferred/waiting/quiet never open fast unhealthy episode."""
    for outcome in (
        FireAttemptOutcome.DEFERRED_LEGAL,
        FireAttemptOutcome.WAITING_ON_WORKER,
        FireAttemptOutcome.NO_ATTEMPT_QUIET,
    ):
        assert is_declared_wait(outcome)
        assert not compute_unhealthy("6237", outcome, data_dir=data_dir)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_f6_pointer_post_failed_payload_distinct(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F6 — fired_bookkeeping_failed heal framing ≠ pre-fire refuse."""
    from scripts.model_manager.ui.controller.charter_runner import tick_sos

    monkeypatch.setenv("CHARTER_TICK_SOS_CDP", "1")
    monkeypatch.setenv("CHARTER_TICK_HEAL_ENABLED", "1")
    captured: list[dict] = []

    async def fake_cdp(*_a, **kwargs):
        captured.append(kwargs)
        return "exec-1"

    with (
        patch.object(tick_sos, "notify_tick_sos", new=AsyncMock(return_value=True)),
        patch.object(tick_sos, "_post_cursor_auto_note", new=AsyncMock(return_value=True)),
        patch.object(tick_sos, "_submit_cdp_heal", side_effect=fake_cdp),
        patch.object(tick_sos, "claim_tick_sos", return_value=True),
    ):
        await tick_sos.fire_episode_actuator(
            "6237",
            fire_attempt_outcome=FireAttemptOutcome.FIRED_BOOKKEEPING_FAILED,
            fire_attempt_reason="pointer_post_failed",
            worker_thread="agent-bus:9999",
        )
        await tick_sos.fire_episode_actuator(
            "6238",
            fire_attempt_outcome=FireAttemptOutcome.REFUSED_PRE_FIRE,
            fire_attempt_reason="admission_rejected",
        )

    assert captured[0]["fire_attempt_outcome"] == FireAttemptOutcome.FIRED_BOOKKEEPING_FAILED
    assert captured[0]["worker_thread"] == "agent-bus:9999"
    assert captured[1]["fire_attempt_outcome"] == FireAttemptOutcome.REFUSED_PRE_FIRE


@pytest.mark.offline
def test_recurring_refuse_needs_two_ticks(data_dir: Path) -> None:
    assert not compute_unhealthy(
        "6237",
        FireAttemptOutcome.REFUSED_PRE_FIRE,
        stopped_reason=None,
        data_dir=data_dir,
    )
    ledger_age.observe("tick_stall", "6237:refuse", present=True, data_dir=data_dir)
    ledger_age.observe("tick_stall", "6237:refuse", present=True, data_dir=data_dir)
    assert compute_unhealthy(
        "6237",
        FireAttemptOutcome.REFUSED_PRE_FIRE,
        stopped_reason=None,
        data_dir=data_dir,
    )


@pytest.mark.offline
@pytest.mark.asyncio
async def test_escalation_on_episode_open(data_dir: Path) -> None:
    emitted: list[tuple[str, dict]] = []

    async def fake_emit(**kwargs):
        emitted.append(("escalation", kwargs))

    with (
        patch(
            "scripts.model_manager.ui.controller.charter_runner.telemetry.emit_tick_escalation",
            side_effect=fake_emit,
        ),
        patch(
            "scripts.model_manager.ui.controller.charter_runner.tick_sos.fire_episode_actuator",
            new=AsyncMock(return_value={}),
        ),
    ):
        result = await observe_root_health(
            "6237",
            fire_attempt_outcome=FireAttemptOutcome.FIRED_BOOKKEEPING_FAILED,
            fire_attempt_reason="pointer_post_failed",
            stopped_reason="pointer_post_failed",
            data_dir=data_dir,
        )
    assert result.episode_opened is True
    assert emitted
    assert emitted[0][1]["fire_attempt_outcome"] == "fired_bookkeeping_failed"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_episode_refire_emits_escalation_only(data_dir: Path) -> None:
    """TTL refire must not re-page or re-dispatch CDP heal."""
    import time

    from scripts.model_manager.ui.controller.charter_runner import root_health as rh

    store = rh._load_store(data_dir=data_dir)
    now = time.time()
    rh._open_episode(
        store,
        "6237",
        outcome=FireAttemptOutcome.FIRED_BOOKKEEPING_FAILED,
        reason="pointer_post_failed",
        now=now - rh._episode_ttl_s() - 1.0,
    )
    rh._save_store(store, data_dir=data_dir)

    escalations: list[dict] = []
    actuator_calls: list[str] = []

    async def fake_emit(**kwargs):
        escalations.append(kwargs)

    async def fake_actuator(root_id: str, **_kwargs):
        actuator_calls.append(root_id)
        return {}

    with (
        patch(
            "scripts.model_manager.ui.controller.charter_runner.telemetry.emit_tick_escalation",
            side_effect=fake_emit,
        ),
        patch(
            "scripts.model_manager.ui.controller.charter_runner.tick_sos.fire_episode_actuator",
            side_effect=fake_actuator,
        ),
    ):
        result = await observe_root_health(
            "6237",
            fire_attempt_outcome=FireAttemptOutcome.FIRED_BOOKKEEPING_FAILED,
            fire_attempt_reason="pointer_post_failed",
            stopped_reason="pointer_post_failed",
            data_dir=data_dir,
        )

    assert result.episode_refired is True
    assert result.episode_opened is False
    assert escalations
    assert escalations[0]["refired"] is True
    assert actuator_calls == []


@pytest.mark.offline
def test_malformed_stop_state_fail_closed(data_dir: Path) -> None:
    stop_dir = data_dir / "cap-stops"
    stop_dir.mkdir(parents=True)
    (stop_dir / "6237.json").write_text("{not-json", encoding="utf-8")
    caps = CapStore()
    allowed, reason = caps.check("6237")
    assert allowed is False
    assert reason == "stopped:malformed_stop_state"
