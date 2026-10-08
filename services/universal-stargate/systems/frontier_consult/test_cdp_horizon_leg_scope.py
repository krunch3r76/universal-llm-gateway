"""AC3: failed-at-horizon retain is registration or successor_birth_id only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from systems.frontier_consult import cdp_generate_reconcile as reconcile
from systems.frontier_consult.cdp_dispatch_envelope import (
    reset_cdp_dispatch_envelope_for_tests,
)
from systems.frontier_consult.cdp_generate_inflight_ledger import _connect
from systems.frontier_consult.cdp_generate_reconcile import (
    max_open_leg_s,
    reset_cdp_generate_reconcile_for_tests,
    upsert_inflight_leg,
)
from systems.frontier_consult.cdp_horizon_probe import (
    SEATED_CDP_FROM_AGENT,
    leg_scoped_horizon_hit,
)


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_cdp_generate_reconcile_for_tests()
    reset_cdp_dispatch_envelope_for_tests()


def _age(execution_id: str) -> str:
    old = (
        datetime.now(UTC) - timedelta(seconds=max_open_leg_s(1800.0) + 10)
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
    return old


def _leg(execution_id: str) -> None:
    upsert_inflight_leg(
        execution_id=execution_id,
        request_id="req",
        thread_id="9501",
        pointer_turn=1,
        caller_agent="dispatch",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=1800.0,
    )
    reconcile.attach_satellite_execution_id(
        execution_id=execution_id,
        satellite_execution_id="sat-1",
    )


def test_unscoped_seated_turn_is_not_a_hit() -> None:
    turns = [
        {
            "from_agent": SEATED_CDP_FROM_AGENT,
            "subject": "live",
            "body": "unscoped",
            "created_at": "2026-10-08T00:00:00+00:00",
        }
    ]
    assert (
        leg_scoped_horizon_hit(
            turns,
            admitted_at="2026-10-07T00:00:00+00:00",
            registration_id="reg-leg",
        )
        is False
    )


def test_registration_before_admit_does_not_hit() -> None:
    turns = [
        {
            "from_agent": SEATED_CDP_FROM_AGENT,
            "subject": "live",
            "body": "early",
            "cse_registration_id": "reg-leg",
            "created_at": "2026-10-01T00:00:00+00:00",
        }
    ]
    assert (
        leg_scoped_horizon_hit(
            turns,
            admitted_at="2026-10-07T00:00:00+00:00",
            registration_id="reg-leg",
        )
        is False
    )


@pytest.mark.asyncio
async def test_failed_horizon_unscoped_abandons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", lambda *a, **k: None)
    monkeypatch.setattr(
        reconcile,
        "poll_satellite_snapshot",
        AsyncMock(
            return_value={
                "status": "failed",
                "stall_stage": "unreachable",
                "error": "unreachable",
                "url": "https://claude.ai/cowork/cse_abc",
                "cse_registration_id": "reg-leg",
            }
        ),
    )
    monkeypatch.setattr(
        reconcile,
        "fetch_recent_thread_turns",
        AsyncMock(
            return_value=[
                {
                    "from_agent": SEATED_CDP_FROM_AGENT,
                    "subject": "other work",
                    "body": "no registration",
                    "created_at": "2099-01-01T00:00:00+00:00",
                }
            ]
        ),
    )
    _leg("exec-unscoped")
    _age("exec-unscoped")
    await reconcile.reconcile_cdp_inflight_legs()
    leg = reconcile.read_inflight_leg("exec-unscoped")
    assert leg is not None
    assert leg.abandoned is True


@pytest.mark.asyncio
async def test_failed_horizon_registration_match_retains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", lambda *a, **k: None)
    monkeypatch.setattr(
        reconcile,
        "poll_satellite_snapshot",
        AsyncMock(
            return_value={
                "status": "failed",
                "stall_stage": "unreachable",
                "error": "unreachable",
                "url": "https://claude.ai/cowork/cse_abc",
                "cse_registration_id": "reg-leg",
            }
        ),
    )

    async def _turns(_thread: str) -> list[dict[str, Any]]:
        admitted = reconcile.read_inflight_leg("exec-scoped")
        assert admitted is not None
        return [
            {
                "from_agent": SEATED_CDP_FROM_AGENT,
                "subject": "seated",
                "body": "same registration",
                "thread": {"cse_registration_id": "reg-leg"},
                "created_at": "2099-01-01T00:00:00+00:00",
            }
        ]

    monkeypatch.setattr(reconcile, "fetch_recent_thread_turns", _turns)
    _leg("exec-scoped")
    _age("exec-scoped")
    await reconcile.reconcile_cdp_inflight_legs()
    leg = reconcile.read_inflight_leg("exec-scoped")
    assert leg is not None
    assert leg.abandoned is False


@pytest.mark.asyncio
async def test_confirmed_dead_abandons_despite_scoped_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", lambda *a, **k: None)
    monkeypatch.setattr(
        reconcile,
        "poll_satellite_snapshot",
        AsyncMock(return_value={"status": "aborted", "successor_birth_id": "birth-1"}),
    )
    _leg("exec-dead")
    _age("exec-dead")
    await reconcile.reconcile_cdp_inflight_legs()
    leg = reconcile.read_inflight_leg("exec-dead")
    assert leg is not None
    assert leg.abandoned is True


@pytest.mark.asyncio
async def test_successor_birth_id_retains(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reconcile, "publish_cdp_kwargs", lambda *a, **k: None)
    monkeypatch.setattr(
        reconcile,
        "poll_satellite_snapshot",
        AsyncMock(
            return_value={
                "status": "failed",
                "stall_stage": "unreachable",
                "error": "unreachable",
                "url": "https://claude.ai/cowork/cse_abc",
                "successor_birth_id": "birth-9",
            }
        ),
    )
    monkeypatch.setattr(
        reconcile,
        "fetch_recent_thread_turns",
        AsyncMock(
            return_value=[
                {
                    "from_agent": SEATED_CDP_FROM_AGENT,
                    "subject": "hop",
                    "body": "successor birth-9",
                    "created_at": "2099-01-01T00:00:00+00:00",
                }
            ]
        ),
    )
    _leg("exec-birth")
    _age("exec-birth")
    await reconcile.reconcile_cdp_inflight_legs()
    leg = reconcile.read_inflight_leg("exec-birth")
    assert leg is not None
    assert leg.abandoned is False
