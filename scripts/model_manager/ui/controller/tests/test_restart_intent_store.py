"""Unit tests for restart-intent status projection (P3 slice 2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scripts.model_manager.ui.controller.restart_intent_store import (
    STATUS_PENDING_DRAIN,
    Intent,
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
