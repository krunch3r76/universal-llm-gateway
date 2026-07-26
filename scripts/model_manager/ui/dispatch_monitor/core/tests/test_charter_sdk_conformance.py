"""G5.2 slice 2 — charter window_failed + SDK lifecycle conformance (v3 §4/§5/§9)."""

from __future__ import annotations

from .conftest import replay

from scripts.model_manager.ui.dispatch_monitor.core import signals
from scripts.model_manager.ui.dispatch_monitor.core.model import Model
from scripts.model_manager.ui.dispatch_monitor.core.protocols import Event


def _row(rows, key: str, value: str):
    matches = [r for r in rows if getattr(r, key) == value]
    assert len(matches) == 1, f"expected exactly one {key}={value}, got {len(matches)}"
    return matches[0]


def _attention(frame, kind: str, subject: str | None = None):
    items = [i for i in frame.attention if i.kind == kind]
    if subject is not None:
        items = [i for i in items if i.subject == subject]
    assert len(items) == 1, f"expected one {kind} attention, got {len(items)}"
    return items[0]


def test_ac1_window_failed_root_and_attention() -> None:
    """AC1 — window_failed → root failed + charter.root.window_failed crit attention."""
    model, now = replay("charter-window-failed.jsonl")
    frame = model.derive(now)
    root = _row(frame.roots, "root_id", "5811")
    assert root.state == "failed"
    assert root.skip_reason == "worker_failed"
    item = _attention(frame, "charter.root.window_failed", "5811")
    assert item.severity == "crit"


def test_ac2_sdk_terminal_and_delivery_failed_attention() -> None:
    """AC2 — timeout/orphaned terminal + crit; delivery_failed non-terminal + crit."""
    model, now = replay("sdk-lifecycle-slice2.jsonl")
    frame = model.derive(now)

    timeout = _row(frame.sdk, "dispatch_id", "exec-timeout")
    assert timeout.state == "timeout"
    assert timeout.terminal_ms is not None
    assert _attention(frame, "sdk.dispatch.timeout", "exec-timeout").severity == "crit"

    orphan = _row(frame.sdk, "dispatch_id", "exec-orphan")
    assert orphan.state == "orphaned"
    assert orphan.terminal_ms is not None
    assert _attention(frame, "sdk.dispatch.orphaned", "exec-orphan").severity == "crit"

    delivery = _row(frame.sdk, "dispatch_id", "exec-deliv")
    assert delivery.state == "completed"
    assert delivery.terminal_ms is None
    assert delivery.delivery_failed is True
    assert _attention(frame, "sdk.dispatch.delivery_failed", "exec-deliv").severity == "crit"


def test_ac3_queued_gs2_git_worker_and_stargate() -> None:
    """AC3 — worker.queued handled for git_worker and stargate payload shapes."""
    model, now = replay("sdk-lifecycle-slice2.jsonl")
    frame = model.derive(now)

    git = _row(frame.sdk, "dispatch_id", "exec-q-git")
    assert git.state == "running", "promoted after queued"
    assert git.queue_position is None

    stargate = _row(frame.sdk, "dispatch_id", "exec-stargate-q")
    assert stargate.state == "queued"
    assert stargate.queue_position == 1


def test_ac4_park_restore_cycle() -> None:
    """AC4 — park_enter → parked_waiting; park_restore returns prior running state."""
    from scripts.model_manager.ui.dispatch_monitor.core.replay import load_fixture

    from .conftest import fixture_path

    records = list(load_fixture(fixture_path("sdk-lifecycle-slice2.jsonl")))
    enter_idx = next(
        i for i, record in enumerate(records) if record.signal == signals.SDK_LEASE_PARK_ENTER
    )
    partial = Model()
    for record in records[: enter_idx + 1]:
        partial.apply(record)
    parked = _row(partial.derive(records[enter_idx].ts_unix_ms).sdk, "dispatch_id", "exec-park-parent")
    assert parked.state == "parked_waiting"

    for record in records[enter_idx + 1 :]:
        partial.apply(record)
    restored = _row(partial.derive(records[-1].ts_unix_ms).sdk, "dispatch_id", "exec-park-parent")
    assert restored.state == "running"


def test_ac5_closeout_relocated() -> None:
    """AC5 — closeout.relocated sets closeout_uri on the dispatch row."""
    model, now = replay("sdk-lifecycle-slice2.jsonl")
    row = _row(model.derive(now).sdk, "dispatch_id", "exec-closeout")
    assert row.closeout_uri == "cortex://notes/system/threads/6107-closeout-body.md"


def test_ac6_charter_lifecycle_handlers() -> None:
    """Charter started/stopped/reloaded handled per v3 §4 (cheap lifecycle)."""
    model = Model()
    model.apply(Event(signals.CHARTER_STARTED, 1_000, {}))
    model.apply(Event(signals.CHARTER_STOPPED, 2_000, {}))
    model.apply(
        Event(
            signals.CHARTER_RELOADED,
            3_000,
            {"modules": ["charter_runner", "observation_event_charter"], "count": 2},
        )
    )
    health = model.derive(3_000).health
    assert health.charter_loop_state == "stopped"
    assert health.charter_last_reload_ms == 3_000
    assert health.charter_reload_module_count == 2


def test_handler_registry_covers_slice2_signals() -> None:
    """Slice 2 signals are in Model handler table."""
    handled = set(Model().handled_signals)
    expected = {
        signals.CHARTER_WINDOW_FAILED,
        signals.CHARTER_STARTED,
        signals.CHARTER_STOPPED,
        signals.CHARTER_RELOADED,
        signals.SDK_WORKER_QUEUED,
        signals.SDK_WORKER_TIMEOUT,
        signals.SDK_WORKER_ORPHANED,
        signals.SDK_WORKER_DELIVERY_FAILED,
        signals.SDK_LEASE_PROMOTED,
        signals.SDK_LEASE_RELEASED,
        signals.SDK_LEASE_PARK_ENTER,
        signals.SDK_LEASE_PARK_RESTORE,
        signals.SDK_CLOSEOUT_RELOCATED,
    }
    assert expected <= handled
