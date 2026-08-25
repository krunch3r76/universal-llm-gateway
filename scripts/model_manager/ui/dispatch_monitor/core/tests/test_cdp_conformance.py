"""G5.2 slice 1 — CDP conformance to v3 §6 (AC-bound tests)."""

from __future__ import annotations

from .conftest import replay

from scripts.model_manager.ui.dispatch_monitor.core import signals
from scripts.model_manager.ui.dispatch_monitor.core.folds.cdp import DEFAULT_MAX_WALL_S
from scripts.model_manager.ui.dispatch_monitor.core.model import Model
from scripts.model_manager.ui.dispatch_monitor.core.protocols import Event
from scripts.model_manager.ui.dispatch_monitor.core.watch import render


def _row(rows, key: str, value: str):
    matches = [r for r in rows if getattr(r, key) == value]
    assert len(matches) == 1, f"expected exactly one {key}={value}, got {len(matches)}"
    return matches[0]


def test_ac1_handlers_match_v3_section6() -> None:
    """AC1 — handler table covers v3 §6 CDP signals verbatim."""
    handlers = set(Model().cdp.handlers())
    expected = {
        signals.CDP_ADMITTED,
        signals.CDP_SUBMITTED,
        signals.CDP_PROOF,
        signals.CDP_STALLED,
        signals.CDP_DELIVERY_FAILED,
        signals.POLL_HINT_ISSUED,
    }
    assert handlers == expected


def test_ac2_request_id_primary_key_one_row() -> None:
    """AC2 — admitted → submitted → proof on one request_id is one CdpLegRow."""
    model, now = replay("cdp-leg-e2e.jsonl")
    frame = model.derive(now)
    assert len(frame.cdp) == 1
    row = frame.cdp[0]
    assert row.request_id == "cdp-req-e2e-739d"
    assert row.state == "proof"
    assert row.execution_id == "739dcb9ad06a"
    assert row.satellite_execution_id == "sat-739d-proj"
    assert row.proof_present is True


def test_ac3_phantom_handlers_unreachable() -> None:
    """AC3 — five phantom G4 signals are not in the live handler table."""
    handled = set(Model().handled_signals)
    phantoms = set(signals.CDP_PHANTOM)
    assert phantoms.isdisjoint(handled)
    assert signals.CDP_PHANTOM == (
        "cdp.generate.running",
        "cdp.generate.progress",
        "cdp.generate.completed",
        "cdp.generate.failed",
        "cdp.generate.aborted",
    )


def test_ac4_poll_hint_cdp_only() -> None:
    """AC4 — CDP poll hints fold; non-CDP hints are ignored."""
    model = Model()
    model.apply(
        Event(
            signals.POLL_HINT_ISSUED,
            1_000,
            {
                "request_id": "cdp-x",
                "reply_from_agent": "web-anthropic",
                "caller_agent": "dispatch",
                "thread_id": "77",
            },
        )
    )
    model.apply(
        Event(
            signals.POLL_HINT_ISSUED,
            1_100,
            {
                "request_id": "sdk-y",
                "reply_from_agent": "sdk",
                "caller_agent": "cursor",
                "thread_id": "88",
            },
        )
    )
    frame = model.derive(1_200)
    assert len(frame.cdp) == 1
    assert frame.cdp[0].request_id == "cdp-x"
    assert frame.cdp[0].caller_agent == "dispatch"


def test_ac5_attention_classes_on_real_signals() -> None:
    """AC5 — stalled, delivery_failed, wall-approaching attention per v3 §9."""
    model = Model()
    model.apply(
        Event(
            signals.CDP_ADMITTED,
            1_000,
            {"request_id": "wall-r", "execution_id": "e1", "model": "cdp/opus-5", "thread_id": "1"},
        )
    )
    warn_at = 1_000 + (DEFAULT_MAX_WALL_S * 1000 * 2 // 3) + 1
    wall_frame = model.derive(warn_at)
    assert any(i.kind == "cdp.leg.wall_approaching" for i in wall_frame.attention)

    stalled = Model()
    stalled.apply(
        Event(
            signals.CDP_STALLED,
            2_000,
            {"request_id": "st-r", "execution_id": "e2", "stall_stage": "timeout", "error": "x"},
        )
    )
    stalled_frame = stalled.derive(2_000)
    assert _row(stalled_frame.attention, "kind", "cdp.leg.stalled").severity == "crit"

    delivery = Model()
    delivery.apply(
        Event(
            signals.CDP_DELIVERY_FAILED,
            3_000,
            {"request_id": "del-r", "execution_id": "e3", "thread_id": "9", "stall_stage": "post"},
        )
    )
    delivery_frame = delivery.derive(3_000)
    assert _row(delivery_frame.attention, "kind", "cdp.leg.delivery_failed").severity == "crit"


def test_ac6_e2e_fixture_populates_projection() -> None:
    """AC6 — cdp-leg-e2e.jsonl replays end-to-end and --watch renders the leg."""
    model, now = replay("cdp-leg-e2e.jsonl")
    frame = model.derive(now)
    row = _row(frame.cdp, "request_id", "cdp-req-e2e-739d")
    assert row.root_id == "5852"
    assert row.caller_agent == "cursor"
    text = render(frame)
    assert "cdp legs (1)" in text
    assert "739dcb9ad06a" in text or "cdp-req-e2e" in text
    assert "proof" in text


def test_ac7_observation_signals_not_unhandled_or_terminal() -> None:
    """AC (e) — observation signals are ignored without fold or unhandled count."""
    payloads = {
        signals.CDP_COMPOSE_ATTESTED: {
            "request_id": "obs-req",
            "execution_id": "obs-exec",
            "satellite_execution_id": "obs-sat",
        },
        signals.CDP_RECONCILED: {
            "request_id": "obs-req",
            "execution_id": "obs-exec",
            "satellite_execution_id": "obs-sat",
            "via": "reconcile",
        },
        signals.CDP_HORIZON_UNVERIFIABLE: {
            "request_id": "obs-req",
            "execution_id": "obs-exec",
            "satellite_execution_id": "obs-sat",
            "thread_id": "1",
            "stall_stage": "horizon_unverifiable_retained",
        },
    }
    for signal, payload in payloads.items():
        model = Model()
        model.apply(Event(signal, 1_000, payload))
        frame = model.derive(1_100)
        assert frame.health.unhandled_signals == {}
        assert model.cdp.legs.get("obs-req") is None or (
            model.cdp.legs["obs-req"].terminal_ms is None
        )
