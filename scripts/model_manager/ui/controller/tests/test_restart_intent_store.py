"""Unit tests for restart-intent status projection (P3 slice 2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scripts.model_manager.ui.controller.restart_intent_store import (
    STATUS_PENDING_DRAIN,
    Intent,
    RestartIntentStore,
    intent_status_view,
)


def _intent(
    *,
    drain_epoch: int | None = None,
    created_at: str = "2026-06-16T12:00:00+00:00",
) -> Intent:
    return Intent(
        intent_id="intent-1",
        service="git_integration_worker",
        action="restart",
        status=STATUS_PENDING_DRAIN,
        drain_epoch=drain_epoch,
        worker_id=None,
        worker_started_at=None,
        deadline_at="2026-06-16T12:05:00+00:00",
        last_seen_event_seq=0,
        reason="manage restart (deferred drain)",
        kill_boundary_at=None,
        created_at=created_at,
        updated_at=created_at,
    )


def test_intent_status_view_returns_five_exact_keys() -> None:
    now = datetime(2026, 6, 16, 12, 0, 30, tzinfo=UTC)
    view = intent_status_view(_intent(), now=now)

    assert set(view.keys()) == {
        "restart_intent_id",
        "status",
        "drain_epoch",
        "deadline_at",
        "elapsed_s",
    }
    assert view["restart_intent_id"] == "intent-1"
    assert view["status"] == STATUS_PENDING_DRAIN
    assert view["deadline_at"] == "2026-06-16T12:05:00+00:00"


def test_intent_status_view_elapsed_s_from_created_at() -> None:
    created = datetime(2026, 6, 16, 12, 0, 0, tzinfo=UTC)
    now = created + timedelta(seconds=42)
    view = intent_status_view(
        _intent(created_at=created.isoformat()),
        now=now,
    )

    assert view["elapsed_s"] == 42


def test_intent_status_view_pending_drain_passthrough_drain_epoch_none() -> None:
    view = intent_status_view(_intent(drain_epoch=None), now=datetime.now(UTC))

    assert view["drain_epoch"] is None


def test_arm_with_boundary_lost_race_leaves_no_verify(tmp_path) -> None:
    """T2: lost CAS does not arm verifying_activation."""
    from scripts.model_manager.ui.controller.restart_intent_store import (
        STATUS_COMPLETED,
        ArmResultKind,
        RestartIntentStore,
    )

    store = RestartIntentStore(db_path=tmp_path / "restart-intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="d",
        reason="r",
    )
    store.advance(intent.intent_id, status=STATUS_COMPLETED, reason="done")
    result = store.arm_with_boundary(
        intent.intent_id,
        from_statuses=frozenset({STATUS_PENDING_DRAIN}),
        kill_boundary_at="2026-01-01T00:00:00+00:00",
        verify_arm_reason="activation_verify_armed:test",
        completed_reason="activation_not_applicable:test",
        arms_verify=True,
    )
    assert result.kind == ArmResultKind.LOST_RACE
    got = store.get(intent.intent_id)
    assert got is not None and got.status == STATUS_COMPLETED
    assert got.kill_boundary_at is None


def test_s1_refuses_cancelled_and_force(tmp_path) -> None:
    from scripts.model_manager.ui.controller.restart_intent_states import (
        LIVE_STATUSES,
        STATUS_CANCELLED,
        STATUS_FAILED,
        STATUS_FORCE_REQUESTED,
    )

    store = RestartIntentStore(db_path=tmp_path / "restart-intents.db")
    for status in (STATUS_CANCELLED, STATUS_FORCE_REQUESTED):
        intent = store.create_intent(
            service="git_integration_worker",
            action="restart",
            deadline_at="d",
            reason="r",
        )
        store.advance(intent.intent_id, status=status, reason="abort")
        assert (
            store.advance_if_status_in(
                intent.intent_id,
                from_statuses=LIVE_STATUSES,
                to_status=STATUS_FAILED,
                reason="nope",
            )
            == 0
        )
        got = store.get(intent.intent_id)
        assert got is not None and got.status == status


def test_arm_not_applicable_is_atomic(tmp_path) -> None:
    from scripts.model_manager.ui.controller.restart_intent_store import (
        STATUS_COMPLETED,
        STATUS_PENDING_DRAIN,
        ArmResultKind,
    )

    store = RestartIntentStore(db_path=tmp_path / "restart-intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="recycle_giw",
        deadline_at="d",
        reason="r",
    )
    result = store.arm_with_boundary(
        intent.intent_id,
        from_statuses=frozenset({STATUS_PENDING_DRAIN}),
        kill_boundary_at="2026-01-01T00:00:00+00:00",
        verify_arm_reason="activation_verify_armed:test",
        completed_reason="activation_not_applicable:once",
        arms_verify=False,
    )
    assert result.kind == ArmResultKind.NOT_APPLICABLE
    got = store.get(intent.intent_id)
    assert got is not None
    assert got.status == STATUS_COMPLETED
    assert got.kill_boundary_at == "2026-01-01T00:00:00+00:00"
    assert got.status_reason == "activation_not_applicable:once"
