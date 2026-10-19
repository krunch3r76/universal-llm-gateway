"""Tests for consumer-facing restart-intent projections and deadline ceiling semantics."""

from __future__ import annotations

from datetime import UTC, datetime

from scripts.model_manager.ui.controller.restart_intent_consumer import (
    DEADLINE_SEMANTICS,
    project_restart_intent_consumer,
)
from scripts.model_manager.ui.controller.restart_intent_store import Intent


def _intent(**overrides: object) -> Intent:
    base = {
        "intent_id": "311a42e5-1d29-43eb-afc6-0750f7b10c93",
        "service": "git_integration_worker",
        "action": "sync_restart",
        "status": "completed",
        "drain_epoch": 1,
        "worker_id": "worker-1",
        "worker_started_at": "2026-08-12T14:55:54.618273+00:00",
        "deadline_at": "2026-08-12T15:40:39.447241+00:00",
        "last_seen_event_seq": 0,
        "reason": "manage sync_restart (deferred drain)",
        "kill_boundary_at": None,
        "created_at": "2026-08-12T15:30:39.447708+00:00",
        "updated_at": "2026-08-12T15:30:56.379576+00:00",
    }
    base.update(overrides)
    return Intent(**base)  # type: ignore[arg-type]


def test_project_restart_intent_consumer_rewords_deadline_as_ceiling() -> None:
    """Projected consumer view must expose deadline_at as a TTL ceiling, not fire time."""
    now = datetime(2026, 8, 12, 15, 31, 0, tzinfo=UTC)
    view = project_restart_intent_consumer(_intent(), now=now)
    assert view["deadline_ceiling_at"] == "2026-08-12T15:40:39.447241+00:00"
    assert view["deadline_at"] == view["deadline_ceiling_at"]
    assert "NOT the scheduled restart fire time" in view["deadline_semantics"]
    assert view["deadline_semantics"] == DEADLINE_SEMANTICS
    assert view["elapsed_s"] == 21


def test_drain_deferred_result_shape_includes_ceiling_fields() -> None:
    """Deferred drain envelopes must carry reworded deadline ceiling fields for clients."""
    from scripts.model_manager.ui.controller.restart_drain import _drain_deferred_result

    payload = _drain_deferred_result(_intent(status="pending_drain"))
    assert payload["deadline_ceiling_at"] == "2026-08-12T15:40:39.447241+00:00"
    assert "deadline_semantics" in payload
    assert "NOT the scheduled restart fire time" in payload["deadline_semantics"]


def test_failed_with_kill_evidence_projects_fired_unverified() -> None:
    from scripts.model_manager.ui.controller.restart_intent_consumer import (
        project_restart_intent_last,
    )
    from scripts.model_manager.ui.controller.restart_intent_states import (
        STATUS_DRAINED_RESTARTING,
    )

    intent = _intent(
        status="failed",
        status_reason="epoch_check_mismatch:same worker generation",
        kill_boundary_at="2026-08-12T15:31:00+00:00",
        transitions=[
            {"status": "pending_drain", "reason": "armed", "at": "t0"},
            {"status": STATUS_DRAINED_RESTARTING, "reason": "kill_commit", "at": "t1"},
            {"status": "failed", "reason": "epoch_check_mismatch", "at": "t2"},
        ],
    )
    last = project_restart_intent_last(intent)
    assert last is not None
    assert last["status"] == "fired_unverified"
    assert last["executed"] is True


def test_cancelled_without_boundary_stays_cancelled() -> None:
    from scripts.model_manager.ui.controller.restart_intent_consumer import (
        project_restart_intent_last,
    )

    intent = _intent(
        status="cancelled",
        status_reason="cancelled_by_operator",
        transitions=[
            {"status": "pending_drain", "reason": "armed", "at": "t0"},
            {"status": "cancelled", "reason": "cancelled_by_operator", "at": "t1"},
        ],
    )
    last = project_restart_intent_last(intent)
    assert last is not None
    assert last["status"] == "cancelled"
    assert last.get("executed") is False
