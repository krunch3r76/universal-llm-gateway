"""Hermetic tests for GIW steer inject deposit / ack / escalation."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from scripts.mcp_bridge_steer_inject import (
    PendingSteer,
    append_spool_entry,
    mark_delivered,
)
from services.git_integration_worker.cursor_sdk_park_for_restart import ParkRefusal
from services.git_integration_worker.cursor_sdk_steer_inject import (
    SteerDepositResult,
    deposit_steer_directive,
    escalate_idle_to_park,
    poll_delivery_ack,
)


@pytest.fixture
def spool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "steer-spool"
    root.mkdir()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return root


def test_deposit_writes_spool_and_emits(
    spool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[Any] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.emit_frontier_event",
        lambda ev: events.append(ev),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject._deposit_authority_turn",
        lambda **_k: "99",
    )
    result = deposit_steer_directive(
        dispatch_id="disp-dep",
        thread_id="10479",
        directive="check Stargate logs",
        reason="operator steer",
        spool_dir=spool,
    )
    assert result.authority_turn_id == "99"
    assert result.dispatch_id == "disp-dep"
    signals = [ev.signal for ev in events]
    assert "frontier.sdk.steer.inject.requested" in signals
    assert "frontier.sdk.steer.inject.spooled" in signals
    from scripts.mcp_bridge_steer_inject import spool_path

    assert spool_path(spool, "disp-dep").is_file()


def test_poll_delivery_ack_emits_delivered(
    spool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[Any] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.emit_frontier_event",
        lambda ev: events.append(ev),
    )
    deposit = SteerDepositResult(
        dispatch_id="disp-ack",
        entry_id="e-ack",
        authority_turn_id="12",
        spool_path=str(spool / "disp-ack.json"),
    )
    append_spool_entry(
        "disp-ack",
        authority_turn_id="12",
        directive="nudge",
        ttl_s=300,
        spool_dir=spool,
        entry_id="e-ack",
    )
    pending = PendingSteer(
        entry_id="e-ack",
        dispatch_id="disp-ack",
        authority_turn_id="12",
        directive="nudge",
        deposited_at="2026-09-13T00:00:00+00:00",
        ttl_s=300,
    )
    mark_delivered(pending, spool_dir=spool)
    row = poll_delivery_ack(deposit, spool_dir=spool)
    assert row is not None
    assert any(ev.signal == "frontier.sdk.steer.inject.delivered" for ev in events)


def test_escalate_idle_to_park_emits_and_signals_park(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[Any] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.emit_frontier_event",
        lambda ev: events.append(ev),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.signal_park",
        lambda *_a, **_k: MagicMock(
            dispatch_id="disp-esc",
            thread_id="10479",
            refusal=ParkRefusal.NOT_LIVE_HERE,
        ),
    )
    deposit = SteerDepositResult(
        dispatch_id="disp-esc",
        entry_id="e-esc",
        authority_turn_id="5",
        spool_path="/tmp/x",
    )
    escalate_idle_to_park(deposit, reason="idle without MCP tool-call ack")
    assert any(ev.signal == "frontier.sdk.steer.inject.escalated" for ev in events)
