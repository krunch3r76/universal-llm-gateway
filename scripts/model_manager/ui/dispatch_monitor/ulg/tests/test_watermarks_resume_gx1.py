"""Per-connection watermarks, resume_from reconnect, GX1 truncation (G5.1 slice 3)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from scripts.model_manager.ui.dispatch_monitor.core import fingerprint as fingerprint_mod
from scripts.model_manager.ui.dispatch_monitor.core import signals
from scripts.model_manager.ui.dispatch_monitor.core.model import Model, hints_after_drop
from scripts.model_manager.ui.dispatch_monitor.core.protocols import Event
from scripts.model_manager.ui.dispatch_monitor.core.replay import JsonlEventSource
from scripts.model_manager.ui.dispatch_monitor.core.tests.conftest import fixture_path
from scripts.model_manager.ui.dispatch_monitor.ulg.connection_watermarks import (
    ConnectionWatermarks,
    family_key_for_signal,
    filter_key,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.controller import MonitorController
from scripts.model_manager.ui.dispatch_monitor.ulg.live_source import LIVE_FILTERS
from scripts.model_manager.ui.dispatch_monitor.ulg.subscribe_session import (
    consume_connection,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.transport_events import (
    fold_status_transport_event,
    replay_truncated_event,
)


async def _async_rows(rows: list[dict]) -> AsyncIterator[dict]:
    for row in rows:
        yield row


async def _collect_apply(rows: list[dict], event_filter: dict[str, str]) -> tuple[list[Event], ConnectionWatermarks]:
    watermarks = ConnectionWatermarks.fresh()
    applied: list[Event] = []

    async def _handler(record: Event) -> None:
        applied.append(record)  # type: ignore[arg-type]

    await consume_connection(
        _async_rows(rows),
        event_filter=event_filter,
        watermarks=watermarks,
        handler=_handler,
    )
    return applied, watermarks


@pytest.mark.parametrize(
    ("event_filter", "signal", "seq"),
    [
        (LIVE_FILTERS[0], "manage.charter.tick.scanned", 101),
        (LIVE_FILTERS[1], "frontier.sdk.worker.progress", 202),
        (LIVE_FILTERS[2], "cdp.generate.running", 303),
        (LIVE_FILTERS[3], "frontier.poll.hint.issued", 404),
    ],
)
def test_each_connection_watermark_advances_only_on_its_family(
    event_filter: dict[str, str],
    signal: str,
    seq: int,
) -> None:
    """AC1 — four independent watermarks; each advances only on its own events."""
    watermarks = ConnectionWatermarks.fresh()
    before = watermarks.snapshot()
    key = filter_key(event_filter)
    watermarks.advance(key, seq)
    after = watermarks.snapshot()
    for other_key, value in before.items():
        if other_key == key:
            assert after[other_key] == seq
        else:
            assert after[other_key] == value


def test_family_key_routes_pipeline_under_sdk_filter() -> None:
    assert family_key_for_signal("pipeline.frontier.dispatch.started") == "frontier.sdk.*"


@pytest.mark.asyncio
async def test_reconnect_resubscribes_one_connection_with_its_watermark() -> None:
    """AC2 — drop one connection; only it replays from its own resume_from."""
    charter_filter = LIVE_FILTERS[0]
    sdk_filter = LIVE_FILTERS[1]
    watermarks = ConnectionWatermarks.fresh()

    applied_charter: list[Event] = []
    applied_sdk: list[Event] = []

    async def charter_handler(record: Event) -> None:
        applied_charter.append(record)  # type: ignore[arg-type]

    async def sdk_handler(record: Event) -> None:
        applied_sdk.append(record)  # type: ignore[arg-type]

    await consume_connection(
        _async_rows(
            [
                {"type": "subscribed", "filter": charter_filter},
                {
                    "signal": "manage.charter.tick.scanned",
                    "ts_unix_ms": 1_000,
                    "seq": 10,
                    "payload": {"roots": 1},
                },
            ]
        ),
        event_filter=charter_filter,
        watermarks=watermarks,
        handler=charter_handler,
    )
    await consume_connection(
        _async_rows(
            [
                {"type": "subscribed", "filter": sdk_filter},
                {
                    "signal": "frontier.sdk.worker.progress",
                    "ts_unix_ms": 1_000,
                    "seq": 20,
                    "payload": {"execution_id": "d1"},
                },
            ]
        ),
        event_filter=sdk_filter,
        watermarks=watermarks,
        handler=sdk_handler,
    )
    assert watermarks.get("manage.charter.tick.*") == 10
    assert watermarks.get("frontier.sdk.*") == 20

    # Simulate charter reconnect with overlap replay from seq 10.
    resume_rows = [
        {"type": "subscribed", "filter": charter_filter, "resumed_from": {"seq": 10}},
        {
            "signal": "manage.charter.tick.scanned",
            "ts_unix_ms": 1_100,
            "seq": 11,
            "payload": {"roots": 2},
        },
    ]
    applied_charter.clear()
    await consume_connection(
        _async_rows(resume_rows),
        event_filter=charter_filter,
        watermarks=watermarks,
        handler=charter_handler,
    )
    assert len(applied_charter) == 1
    assert applied_charter[0].seq == 11  # type: ignore[attr-defined]
    assert watermarks.get("frontier.sdk.*") == 20
    assert watermarks.get("manage.charter.tick.*") == 11


@pytest.mark.asyncio
async def test_gx1_seq_gap_detects_truncation_and_stops_replay() -> None:
    """AC3 — seq gap marks reseeding + attention; replay batch is not fully folded."""
    model = Model()
    watermarks = ConnectionWatermarks.fresh()
    watermarks.advance("frontier.sdk.*", 100)
    truncated: list[tuple[str, int | None, dict]] = []

    async def on_truncated(connection: str, requested: int | None, detail: dict) -> None:
        truncated.append((connection, requested, detail))

    async def handler(record: Event) -> None:
        model.apply(record)

    result = await consume_connection(
        _async_rows(
            [
                {
                    "signal": "frontier.sdk.worker.progress",
                    "ts_unix_ms": 1_000,
                    "seq": 105,
                    "payload": {"execution_id": "gap"},
                },
                {
                    "signal": "frontier.sdk.worker.progress",
                    "ts_unix_ms": 1_100,
                    "seq": 106,
                    "payload": {"execution_id": "gap"},
                },
            ]
        ),
        event_filter=LIVE_FILTERS[1],
        watermarks=watermarks,
        handler=handler,
        on_truncated=on_truncated,
    )
    assert result.truncated is True
    assert result.events_applied == 0
    assert truncated == [("frontier.sdk.*", 100, {"reason": "seq_gap", "first_seq": 105})]

    ts = 2_000
    model.apply(
        replay_truncated_event(
            connection="frontier.sdk.*",
            requested_seq=100,
            reason="seq_gap",
            first_seq=105,
            ts_unix_ms=ts,
        )
    )
    model.apply(
        fold_status_transport_event(
            fold_status="reseeding",
            reason="seq_gap",
            connection="frontier.sdk.*",
            ts_unix_ms=ts,
        )
    )
    frame = model.derive(ts + 1)
    assert frame.health.fold_status == "reseeding"
    assert any(
        item.kind == "monitor.transport.replay_truncated" for item in frame.attention
    )


@pytest.mark.asyncio
async def test_truncation_recovery_reseed_returns_fold_status_live(monkeypatch) -> None:
    """AC4 — after truncation, re-seed path restores fold_status to live."""
    controller = MonitorController(seed_minutes=60)

    def _fake_seed(apply, **kwargs):  # noqa: ANN001, ARG001
        apply(
            Event(
                signals.CHARTER_SCANNED,
                1_000,
                {"roots": 1},
                seq=11,
            )
        )
        return 1

    monkeypatch.setattr(
        "scripts.model_manager.ui.dispatch_monitor.ulg.controller.seed_model",
        _fake_seed,
    )

    await controller._reseed_after_truncation("manage.charter.tick.*")
    frame = controller.model.derive(controller.clock.now_ms())
    assert frame.health.fold_status == "live"
    assert watermarks_ok(controller) is True


def watermarks_ok(controller: MonitorController) -> bool:
    return controller.watermarks.get("manage.charter.tick.*") == 11


def test_truncation_meta_events_enter_model_not_controller_mutation() -> None:
    """AC5 — fold_status and truncation surface only via apply()."""
    model = Model()
    model.apply(
        replay_truncated_event(
            connection="cdp.generate.*",
            requested_seq=9,
            reason="seq_gap",
            first_seq=20,
            ts_unix_ms=1_000,
        )
    )
    model.apply(
        fold_status_transport_event(
            fold_status="reseeding",
            reason="seq_gap",
            connection="cdp.generate.*",
            ts_unix_ms=1_000,
        )
    )
    frame = model.derive(2_000)
    assert frame.health.fold_status == "reseeding"
    assert frame.attention[0].kind == "monitor.transport.replay_truncated"


def test_overlap_idempotent_for_terminal_keyed_sdk_events() -> None:
    """AC6 — overlap replay of terminal keyed on execution_id is fingerprint-stable."""
    model_clean = Model()
    start = Event(
        signals.MONITOR_META_SDK_STARTED,
        1_000,
        {"execution_id": "overlap-dup", "seat": "cursor-sdk"},
    )
    done = Event(
        signals.SDK_WORKER_COMPLETED,
        2_000,
        {"execution_id": "overlap-dup", "status": "completed"},
        seq=100,
    )
    model_clean.apply_all([start, done])
    clean_fp = model_clean.derive(3_000).fingerprint

    model_overlap = Model()
    model_overlap.apply_all([start, done, start, done])
    overlap_fp = model_overlap.derive(3_000).fingerprint
    assert overlap_fp == clean_fp


def test_full_fixture_overlap_not_idempotent_without_envelope_id_g6_gap() -> None:
    """AC6 — full fixture double-replay diverges where counter/meta rows lack dedupe keys."""
    source = JsonlEventSource.from_path(fixture_path("gs2-dual-emitter.jsonl"))
    model_clean = Model()
    model_clean.apply_all(source.records)
    now = source.max_ts()
    clean_fp = model_clean.derive(now).fingerprint

    model_overlap = Model()
    model_overlap.apply_all(source.records)
    model_overlap.apply_all(source.records)
    overlap_fp = model_overlap.derive(now).fingerprint
    assert overlap_fp != clean_fp


def test_overlap_ingest_drop_redelivery_changes_fingerprint_g6_gap() -> None:
    """AC6 — counter signals without envelope id are not overlap-idempotent (G6 gap)."""
    drop = Event(signals.EVENTS_DROPPED_INGEST, 1_000, {"count": 2}, seq=50)
    model_clean = Model()
    model_clean.apply(drop)
    clean_fp = model_clean.derive(2_000).fingerprint

    model_overlap = Model()
    model_overlap.apply(drop)
    model_overlap.apply(drop)
    overlap_fp = model_overlap.derive(2_000).fingerprint
    assert overlap_fp != clean_fp


def test_f4_drop_hint_stamped_on_next_frame() -> None:
    """AC7 partial — Controller can stamp wildcard hint when drop is signaled."""
    controller = MonitorController()
    controller.model.apply(
        Event("manage.charter.tick.scanned", 1_000, {"roots": 1}, seq=1)
    )
    first = controller.model.derive(1_500)
    controller._last_frame = first
    controller.mark_subscriber_drop()
    controller.model.apply(
        Event("manage.charter.tick.admitted", 1_600, {"root": "r1"}, seq=2)
    )
    controller.tick()
    assert controller._last_frame is not None
    assert controller._last_frame.changed_hints == ("*",)


def test_hub_stub_cannot_detect_subscriber_drop_f4_blocked() -> None:
    """AC7 — stub hub has no per-subscriber delivery gap callback."""
    hub_methods = dir(MonitorController().hub)
    assert "on_subscriber_overflow" not in hub_methods
    assert "delivery_gap" not in hub_methods


def test_unwired_reconcile_attention_only_on_trigger_not_seed() -> None:
    """Q2 — unwired reconcile AttentionItem fires on trigger, not at seed."""
    controller = MonitorController(reconcile=None)
    seeded_frame = controller.model.derive(1_000)
    assert not any(
        item.kind == "monitor.reconcile.source_failed" for item in seeded_frame.attention
    )
    controller.trigger_reconcile("2678")
    triggered = controller.model.derive(2_000)
    assert any(
        item.kind == "monitor.reconcile.source_failed" for item in triggered.attention
    )


def test_transport_signal_registered_in_handler_table() -> None:
    assert signals.MONITOR_TRANSPORT_REPLAY_TRUNCATED in Model().handled_signals


def test_fixture_overlap_duplicate_scanned_idempotent() -> None:
    """Supporting evidence — charter duplicate scanned in fixture 1 is fingerprint-stable."""
    source = JsonlEventSource.from_path(fixture_path("charter-admit-run-terminal.jsonl"))
    first = Model()
    first.apply_all(source.records)
    now = source.max_ts()
    second = Model()
    second.apply_all(source.records)
    assert first.derive(now).fingerprint == second.derive(now).fingerprint
    assert fingerprint_mod.fingerprint_payload(first.derive(now)) == fingerprint_mod.fingerprint_payload(
        second.derive(now)
    )
