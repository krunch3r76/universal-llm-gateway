"""G4b ES terminal backfill — completed-present LIVE clear (agent-bus:6563)."""

from __future__ import annotations

import json

from scripts.model_manager.ui.dispatch_monitor.core import signals
from scripts.model_manager.ui.dispatch_monitor.core.board_lines import live_sdk
from scripts.model_manager.ui.dispatch_monitor.core.model import Model
from scripts.model_manager.ui.dispatch_monitor.core.protocols import Event
from scripts.model_manager.ui.dispatch_monitor.ulg.controller import MonitorController
from scripts.model_manager.ui.dispatch_monitor.ulg.event_query import (
    row_matches_dispatch,
    worker_terminals_for_dispatch,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.reconcile_events import (
    events_from_es_worker_terminals,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.seeder import seed_model
from scripts.model_manager.ui.dispatch_monitor.ulg.terminal_backfill import (
    backfill_missing_terminals,
    backfill_sdk_fold,
)


def _completed_es_row(
    dispatch_id: str,
    *,
    ts_unix_ms: int = 1_756_000_000_000,
) -> dict:
    return {
        "signal": signals.SDK_WORKER_COMPLETED,
        "ts_unix_ms": ts_unix_ms,
        "payload": {
            "dispatch_id": dispatch_id,
            "execution_id": dispatch_id,
            "status": "completed",
            "outcome": "completed",
        },
    }


def test_row_matches_dispatch_id_and_execution_id() -> None:
    dispatch_id = "auto-960d8a33296a"
    assert row_matches_dispatch(
        {"payload": {"dispatch_id": dispatch_id}},
        dispatch_id,
    )
    assert row_matches_dispatch(
        {"payload": {"execution_id": dispatch_id}},
        dispatch_id,
    )
    assert not row_matches_dispatch(
        {"payload": {"dispatch_id": "other"}},
        dispatch_id,
    )


def test_events_from_es_worker_terminals_stamps_provenance() -> None:
    dispatch_id = "exec-backfill-prov"
    events = events_from_es_worker_terminals(
        [_completed_es_row(dispatch_id)],
        dispatch_id=dispatch_id,
    )
    assert len(events) == 1
    event = events[0]
    assert event.signal == signals.SDK_WORKER_COMPLETED
    assert event.payload[signals.PROVENANCE_RECONCILED_KEY] == (
        signals.PROVENANCE_RECONCILED
    )
    assert event.payload["dispatch_id"] == dispatch_id


def test_backfill_clears_live_when_es_has_completed() -> None:
    """AC5: progress-only fold + ES completed → terminal_ms set, not in live_sdk."""
    model = Model()
    dispatch_id = "exec-completed-present"
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_000,
            {"dispatch_id": dispatch_id, "execution_id": dispatch_id},
        )
    )
    model.apply(
        Event(
            signals.SDK_WORKER_PROGRESS,
            2_000,
            {"dispatch_id": dispatch_id, "execution_id": dispatch_id},
        )
    )

    def _query(_dispatch_id: str, *, minutes: int = 60, **kwargs) -> list[dict]:
        assert _dispatch_id == dispatch_id
        return [_completed_es_row(dispatch_id, ts_unix_ms=3_000)]

    applied = backfill_sdk_fold(model.apply, model.sdk, query_terminals=_query)
    assert applied == 1
    frame = model.derive(10_000)
    row = next(r for r in frame.sdk if r.dispatch_id == dispatch_id)
    assert row.terminal_ms == 3_000
    assert live_sdk(frame.sdk) == []


def test_lease_without_terminal_backfill_clears_live_and_attention() -> None:
    """AC6: lease flag + ES completed → terminal clears LIVE and crit attention."""
    model = Model()
    dispatch_id = "exec-lease-backfill"
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_000,
            {"dispatch_id": dispatch_id, "execution_id": dispatch_id},
        )
    )
    model.apply(
        Event(
            signals.SDK_LEASE_RELEASED,
            2_000,
            {"dispatch_id": dispatch_id, "source_repo": "universal-llm-gateway"},
        )
    )
    pre = model.derive(5_000)
    assert any(
        i.kind == "sdk.dispatch.lease_released_without_terminal"
        for i in pre.attention
    )

    def _query(_dispatch_id: str, *, minutes: int = 60, **kwargs) -> list[dict]:
        return [_completed_es_row(dispatch_id, ts_unix_ms=4_000)]

    backfill_sdk_fold(model.apply, model.sdk, query_terminals=_query)
    post = model.derive(10_000)
    row = next(r for r in post.sdk if r.dispatch_id == dispatch_id)
    assert row.terminal_ms == 4_000
    assert not any(
        i.kind == "sdk.dispatch.lease_released_without_terminal"
        for i in post.attention
    )


def test_lease_without_terminal_empty_es_stays_live_with_attention() -> None:
    """AC7: true emit-gap — no ES terminal → LIVE + attention unchanged."""
    model = Model()
    dispatch_id = "exec-true-gap"
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_000,
            {"dispatch_id": dispatch_id, "execution_id": dispatch_id},
        )
    )
    model.apply(
        Event(
            signals.SDK_LEASE_RELEASED,
            2_000,
            {"dispatch_id": dispatch_id},
        )
    )

    def _query(_dispatch_id: str, *, minutes: int = 60, **kwargs) -> list[dict]:
        return []

    applied = backfill_sdk_fold(model.apply, model.sdk, query_terminals=_query)
    assert applied == 0
    frame = model.derive(10_000)
    row = next(r for r in frame.sdk if r.dispatch_id == dispatch_id)
    assert row.terminal_ms is None
    assert row.lease_released_without_terminal is True
    assert any(
        i.kind == "sdk.dispatch.lease_released_without_terminal"
        for i in frame.attention
    )


def test_seed_path_backfill_extended_lookback(monkeypatch) -> None:
    """AC8: seed omits aged completed; post-seed backfill applies it."""
    dispatch_id = "exec-seed-aged-complete"
    model = Model()

    def _audit(**_kwargs):
        return {}

    def _signals(signal: str, *, minutes: int = 60, limit: int = 500, sock=None):
        if signal in (signals.SDK_WORKER_DISPATCHED, "frontier.sdk.*"):
            return [
                {
                    "signal": signals.SDK_WORKER_DISPATCHED,
                    "ts_unix_ms": 1_000,
                    "payload": json.dumps(
                        {
                            "dispatch_id": dispatch_id,
                            "execution_id": dispatch_id,
                        }
                    ),
                }
            ]
        return []

    backfill_calls: list[int] = []

    def _query(_dispatch_id: str, *, minutes: int = 60, **kwargs) -> list[dict]:
        backfill_calls.append(minutes)
        return [_completed_es_row(dispatch_id, ts_unix_ms=9_000_000)]

    monkeypatch.setattr(
        "scripts.model_manager.ui.dispatch_monitor.ulg.seeder.charter_tick_audit",
        _audit,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.dispatch_monitor.ulg.seeder.signal_events",
        _signals,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.dispatch_monitor.ulg.seeder.fetch_lease_snapshot",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.dispatch_monitor.ulg.terminal_backfill.worker_terminals_for_dispatch",
        _query,
    )

    seed_model(
        model.apply,
        minutes=60,
        sdk_fold=model.sdk,
        backfill_minutes=24 * 60,
    )
    assert backfill_calls == [24 * 60]
    frame = model.derive(10_000_000)
    row = next(r for r in frame.sdk if r.dispatch_id == dispatch_id)
    assert row.terminal_ms == 9_000_000
    assert live_sdk(frame.sdk) == []


def test_backfill_missing_terminals_caps_ids() -> None:
    applied_ids: list[str] = []

    def _apply(event: Event) -> None:
        applied_ids.append(str(event.payload.get("dispatch_id")))

    def _query(dispatch_id: str, *, minutes: int = 60, **kwargs) -> list[dict]:
        return [_completed_es_row(dispatch_id, ts_unix_ms=1_000)]

    count = backfill_missing_terminals(
        _apply,
        [f"id-{index}" for index in range(40)],
        max_ids=32,
        query_terminals=_query,
    )
    assert count == 32
    assert len(applied_ids) == 32


def test_worker_terminals_for_dispatch_filters_payload(monkeypatch) -> None:
    dispatch_id = "auto-960d8a33296a"
    rows_seen: list[str] = []

    def _signal_events(signal: str, *, minutes: int = 60, limit: int = 500, sock=None):
        rows_seen.append(signal)
        if signal == signals.SDK_WORKER_COMPLETED:
            return [
                _completed_es_row(dispatch_id),
                _completed_es_row("other-id"),
            ]
        return []

    result = worker_terminals_for_dispatch(
        dispatch_id,
        minutes=60,
        signal_events_fn=_signal_events,
    )
    assert len(result) == 1
    assert signals.SDK_WORKER_COMPLETED in rows_seen


def test_controller_tick_backfills_lease_without_terminal(monkeypatch) -> None:
    controller = MonitorController()
    dispatch_id = "exec-controller-backfill"
    controller.model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_000,
            {"dispatch_id": dispatch_id, "execution_id": dispatch_id},
        )
    )
    controller.model.apply(
        Event(
            signals.SDK_LEASE_RELEASED,
            2_000,
            {"dispatch_id": dispatch_id},
        )
    )

    def _query(_dispatch_id: str, *, minutes: int = 60, **kwargs) -> list[dict]:
        return [_completed_es_row(dispatch_id, ts_unix_ms=5_000)]

    monkeypatch.setattr(
        "scripts.model_manager.ui.dispatch_monitor.ulg.controller.backfill_sdk_fold",
        lambda apply, sdk, **kwargs: backfill_sdk_fold(
            apply, sdk, query_terminals=_query, **kwargs
        ),
    )
    controller.tick()
    row = next(
        r for r in controller.model.derive(10_000).sdk if r.dispatch_id == dispatch_id
    )
    assert row.terminal_ms == 5_000


def test_falsifier_shape_auto_960d8a33296a() -> None:
    """AC9: completed-present zombie class — ES completed clears LIVE after backfill."""
    dispatch_id = "auto-960d8a33296a"
    model = Model()
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_756_000_000_000,
            {"dispatch_id": dispatch_id, "execution_id": dispatch_id},
        )
    )
    model.apply(
        Event(
            signals.SDK_WORKER_PROGRESS,
            1_756_000_100_000,
            {"dispatch_id": dispatch_id, "execution_id": dispatch_id},
        )
    )
    completed_at = 1_756_000_200_000

    def _query(_dispatch_id: str, *, minutes: int = 60, **kwargs) -> list[dict]:
        return [_completed_es_row(dispatch_id, ts_unix_ms=completed_at)]

    backfill_sdk_fold(
        model.apply,
        model.sdk,
        minutes=24 * 60,
        query_terminals=_query,
    )
    linger_ms = completed_at + (6 * 60 + 45) * 60 * 1000
    frame = model.derive(linger_ms)
    assert live_sdk(frame.sdk) == []
    row = next(r for r in frame.sdk if r.dispatch_id == dispatch_id)
    assert row.terminal_ms == completed_at
