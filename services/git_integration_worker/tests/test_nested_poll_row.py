"""cursor-auto poll must follow park_resumed_by instead of newest-on-thread."""

from __future__ import annotations

from types import SimpleNamespace

from services.git_integration_worker.cursor_auto.nested_poll_row import (
    resolve_nested_poll_row,
)


def test_follow_resume_child_when_parent_cancelled(
    monkeypatch: object,
) -> None:
    parent = {"dispatch_id": "auto-a479e8e67316", "status": "cancelled"}
    child = {"dispatch_id": "auto-a479e8e67316-r1", "status": "completed"}
    ledger = SimpleNamespace(
        dispatch_status_by_id=lambda dispatch_id: (
            parent if dispatch_id == parent["dispatch_id"] else child
        ),
        dispatch_status_by_thread=lambda thread_id: child,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_poll_row._park_resume_child",
        lambda dispatch_id: child["dispatch_id"],
    )
    row = resolve_nested_poll_row(
        ledger, thread_id="11667", dispatch_id=parent["dispatch_id"]
    )
    assert row == child


def test_by_id_running_parent_not_displaced_by_newer_thread_row() -> None:
    parent = {"dispatch_id": "auto-parent", "status": "running"}
    newer = {"dispatch_id": "auto-other", "status": "completed"}
    ledger = SimpleNamespace(
        dispatch_status_by_id=lambda dispatch_id: parent,
        dispatch_status_by_thread=lambda thread_id: newer,
    )
    row = resolve_nested_poll_row(
        ledger, thread_id="11667", dispatch_id="auto-parent"
    )
    assert row == parent
