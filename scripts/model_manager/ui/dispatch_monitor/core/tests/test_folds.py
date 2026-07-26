"""Fold behaviour, stated as the negative space each fold must respect.

Most of these assert what the folds must *not* conclude. That is deliberate: the
expensive defects in a monitor are not missing rows, they are confidently wrong
rows -- silence read as failure, a window close read as a root close, a judgment
gap read as a dispatch.
"""

from __future__ import annotations

from .conftest import replay

from scripts.model_manager.ui.dispatch_monitor.core import signals
from scripts.model_manager.ui.dispatch_monitor.core.model import Model
from scripts.model_manager.ui.dispatch_monitor.core.protocols import Event


def _row(rows, key: str, value: str):
    """Return the single row whose ``key`` attribute equals ``value``."""
    matches = [r for r in rows if getattr(r, key) == value]
    assert len(matches) == 1, f"expected exactly one {key}={value}, got {len(matches)}"
    return matches[0]


# --- charter ---------------------------------------------------------------
def test_happy_path_root_closes_and_dispatch_completes() -> None:
    """The admit-to-terminal fixture folds to a closed root and a completed leg."""
    model, now = replay("charter-admit-run-terminal.jsonl")
    frame = model.derive(now)
    root = _row(frame.roots, "root_id", "5852")
    assert root.state == "closed"
    assert root.closed is True and root.unenrolled is True
    assert root.window_index == 4
    assert root.checkpoint_turn == 7
    assert root.project == "project:dispatch-supervisor-monitor"
    dispatch = _row(frame.sdk, "dispatch_id", "exec-9a1f")
    assert dispatch.state == "completed"
    assert dispatch.duration_ms == 179_000
    assert dispatch.cached_tokens == 30_000
    assert dispatch.root_id == "5852", "worker_thread must correlate back to the root"


def test_window_close_does_not_close_the_root() -> None:
    """``.closed`` is window-shaped. Only ``root_closed`` may close a root."""
    model = Model()
    model.apply(
        Event(
            "manage.charter.tick.admitted",
            1_000,
            {"root": "7000", "worker_thread": "7001", "window_index": 1},
        )
    )
    model.apply(
        Event(
            "manage.charter.tick.closed",
            2_000,
            {"root": "7000", "window_index": 1, "worker_thread": "7001",
             "worker_closed": True},
        )
    )
    root = _row(model.derive(3_000).roots, "root_id", "7000")
    assert root.state == "window_closed"
    assert root.closed is False, "a harvest window close is not a root close"


def test_admitted_zero_is_not_a_health_fault() -> None:
    """A scan that admits nothing is a decision, not a degradation."""
    model = Model()
    model.apply(
        Event("manage.charter.tick.scanned", 1_000, {"roots": 4, "admitted": 0})
    )
    health = model.derive(1_000).health
    assert health.tick_roots_scanned == 4
    assert health.tick_admitted_last_scan == 0
    assert health.degraded == ()


def test_arc_g_step_mirrors_admission_payload_only() -> None:
    """``arc_g_step`` comes from the payload or stays ``None``. No other route."""
    model, now = replay("parked-parent.jsonl")
    mirrored = _row(model.derive(now).roots, "root_id", "5735")
    assert mirrored.arc_g_step == "G4"
    assert mirrored.arc_g_step_label == "R-implement"
    bare = Model()
    bare.apply(Event("manage.charter.tick.admitted", 1_000, {"root": "8000"}))
    assert _row(bare.derive(1_000).roots, "root_id", "8000").arc_g_step is None


def test_arcs_is_present_but_empty_in_v1(any_fixture: str) -> None:
    """``arcs`` is declared and always empty until GP1 ships ``checkpoint_folded``."""
    model, now = replay(any_fixture)
    assert model.derive(now).arcs == {}


# --- parked parent (cross-family) -----------------------------------------
def test_parked_parent_requires_both_folds() -> None:
    """Terminal worker leg plus an open root derives ``parked``."""
    model, now = replay("parked-parent.jsonl")
    frame = model.derive(now)
    root = _row(frame.roots, "root_id", "5735")
    assert root.state == "parked"
    assert root.worker_thread == "5990"
    assert any(i.kind == "charter.root.parked" for i in frame.attention)


def test_parked_state_needs_the_correlation_edge() -> None:
    """Without the worker-thread edge, the root stays in flight rather than parked.

    Correlation is evidence-only. A dispatch that never names its thread or root
    must not be attached to a root by proximity, so the root cannot be parked.
    """
    model = Model()
    model.apply(Event("manage.charter.tick.admitted", 1_000, {"root": "9000"}))
    model.apply(
        Event("frontier.sdk.worker.started", 1_100, {"execution_id": "lone"})
    )
    model.apply(
        Event(
            "frontier.sdk.worker.completed",
            1_200,
            {"execution_id": "lone", "status": "completed"},
        )
    )
    frame = model.derive(9_000_000)
    assert _row(frame.roots, "root_id", "9000").state == "in_flight"
    assert _row(frame.sdk, "dispatch_id", "lone").root_id is None


def test_admission_alone_creates_no_sdk_row() -> None:
    """A judgment gap -- admitted but never started -- yields no dispatch row."""
    model = Model()
    model.apply(
        Event(
            "manage.charter.tick.admitted",
            1_000,
            {"root": "9100", "worker_thread": "9101"},
        )
    )
    assert model.derive(2_000).sdk == ()


# --- sdk / GS2 -------------------------------------------------------------
def test_agreeing_emitters_do_not_flag_divergence() -> None:
    """Both lanes seen, terminal kept from the first, no divergence."""
    model, now = replay("gs2-dual-emitter.jsonl")
    row = _row(model.derive(now).sdk, "dispatch_id", "exec-gs2-a")
    assert row.emitters_seen == ("worker", "pipeline")
    assert row.divergent_fields == ()
    assert row.terminal_emitter == "worker"
    assert row.terminal_ms == 1753600200000, "the second terminal must not rewrite it"
    assert row.state == "completed"


def test_disagreeing_emitters_report_without_resolving() -> None:
    """GS2 divergence is surfaced as a crit and the first terminal is preserved."""
    model, now = replay("gs2-dual-emitter.jsonl")
    frame = model.derive(now)
    row = _row(frame.sdk, "dispatch_id", "exec-gs2-b")
    assert row.state == "completed", "first terminal wins; the fold picks no winner"
    assert "state" in row.divergent_fields
    assert "failure_reason" in row.divergent_fields
    assert row.emitters_seen == ("worker", "pipeline")
    item = _row(frame.attention, "key", "sdk.emitter.divergence:exec-gs2-b")
    assert item.severity == "crit"


def test_single_emitter_is_not_divergence() -> None:
    """A dispatch seen on one lane only must not be flagged."""
    model, now = replay("gs2-dual-emitter.jsonl")
    row = _row(model.derive(now).sdk, "dispatch_id", "exec-gs2-c")
    assert row.emitters_seen == ("pipeline",)
    assert row.divergent_fields == ()


def test_replayed_terminal_is_idempotent() -> None:
    """A resume_from overlap replaying one terminal changes nothing."""
    model = Model()
    start = Event(
        signals.MONITOR_META_SDK_STARTED,
        1_000,
        {"execution_id": "dup", "seat": "cursor-sdk"},
    )
    done = Event(
        "frontier.sdk.worker.completed",
        2_000,
        {"execution_id": "dup", "status": "completed", "prompt_tokens": 10},
    )
    model.apply_all([start, done])
    once = model.derive(3_000)
    model.apply_all([start, done])
    twice = model.derive(3_000)
    assert once.sdk == twice.sdk
    assert once.fingerprint == twice.fingerprint


# --- cdp (live v3 §6) -------------------------------------------------------
def test_cdp_silence_is_not_failure() -> None:
    """An admitted leg with no terminal stays admitted; wall warn is not failed."""
    model, now = replay("cdp-leg.jsonl")
    frame = model.derive(now + 1_210_000)
    row = _row(frame.cdp, "request_id", "req-silent")
    assert row.state == "admitted"
    assert row.terminal_ms is None
    assert row.failure_reason is None
    wall = _row(frame.attention, "key", "cdp.leg.wall_approaching:req-silent")
    assert wall.severity == "warn"


def test_cdp_proof_terminal_is_clean() -> None:
    """A proof terminal carries proof_present and no failure attention."""
    model, now = replay("cdp-leg.jsonl")
    frame = model.derive(now)
    clean = _row(frame.cdp, "request_id", "req-success")
    assert clean.state == "proof" and clean.proof_present is True
    assert clean.satellite_execution_id == "sat-9a1f"
    assert not any(i.kind == "cdp.leg.stalled" and i.subject == "req-success" for i in frame.attention)


def test_cdp_stalled_raises_attention() -> None:
    """``cdp.generate.stalled`` is a terminal failure with crit attention."""
    model, now = replay("cdp-leg.jsonl")
    frame = model.derive(now)
    row = _row(frame.cdp, "request_id", "req-stalled")
    assert row.state == "stalled" and row.terminal_ms is not None
    item = _row(frame.attention, "key", "cdp.leg.stalled:req-stalled")
    assert item.severity == "crit"


def test_cdp_admitted_submitted_proof_one_row() -> None:
    """Admitted → submitted → proof on one request_id yields exactly one row."""
    model = Model()
    rid = "req-abc"
    model.apply(
        Event(
            "cdp.generate.admitted",
            1_000,
            {"request_id": rid, "execution_id": "ex1", "model": "cdp/opus-5", "thread_id": "99"},
        )
    )
    model.apply(
        Event(
            "cdp.generate.submitted",
            2_000,
            {"request_id": rid, "execution_id": "ex1", "satellite_execution_id": "sat1", "model": "cdp/opus-5"},
        )
    )
    model.apply(
        Event(
            "cdp.generate.proof",
            3_000,
            {"request_id": rid, "execution_id": "ex1", "satellite_execution_id": "sat1", "archive_uri": "cortex://a"},
        )
    )
    frame = model.derive(3_000)
    assert len(frame.cdp) == 1
    row = frame.cdp[0]
    assert row.request_id == rid
    assert row.state == "proof"
    assert row.satellite_execution_id == "sat1"


def test_non_cdp_poll_hint_is_ignored() -> None:
    """Poll hints with reply_from_agent != cdp do not open CDP rows."""
    model, now = replay("cdp-leg.jsonl")
    frame = model.derive(now)
    assert not any(r.request_id == "req-sdk-only" for r in frame.cdp)


def test_cdp_poll_hint_populates_caller_agent() -> None:
    """CDP poll hint is the earliest marker and supplies caller_agent."""
    model, now = replay("cdp-leg.jsonl")
    row = _row(model.derive(now).cdp, "request_id", "req-success")
    assert row.caller_agent == "cursor"
    assert row.thread_id == "5901"



# --- totality --------------------------------------------------------------
def test_unknown_signal_is_counted_never_raised() -> None:
    """Schema drift is reported on the operator's surface, not raised as a crash."""
    model, now = replay("gs2-dual-emitter.jsonl")
    frame = model.derive(now)
    assert frame.health.unhandled_signals == {"some.unknown.signal.family": 1}
    assert "unhandled_signals" in frame.health.degraded
    assert any(i.kind == "signal.unhandled" for i in frame.attention)


def test_charter_error_reads_live_reason_field() -> None:
    """Live ``manage.charter.tick.error`` carries ``reason``, not ``message``."""
    model = Model()
    model.apply(
        Event(
            "manage.charter.tick.error",
            1_000,
            {"reason": "lease acquisition failed: manage.sock refused"},
        )
    )
    health = model.derive(1_000).health
    assert health.tick_last_error_message == "lease acquisition failed: manage.sock refused"


def test_malformed_payload_does_not_crash_the_fold() -> None:
    """Wrong-typed and missing payload fields degrade to ``None``, not exceptions."""
    model = Model()
    model.apply(
        Event(
            "manage.charter.tick.admitted",
            1_000,
            {"root": "9200", "window_index": "not-an-int", "worker_thread": ""},
        )
    )
    row = _row(model.derive(1_000).roots, "root_id", "9200")
    assert row.window_index is None and row.worker_thread is None


def test_ingest_drops_degrade_but_subscribe_drops_do_not() -> None:
    """Lost fold inputs mean incomplete state; View-side drops are correct."""
    ingest, now = replay("gs2-dual-emitter.jsonl")
    assert ingest.derive(now).health.events_dropped_ingest == 2
    assert "fold_inputs_dropped" in ingest.derive(now).health.degraded
    subscribe, sub_now = replay("cdp-leg.jsonl")
    health = subscribe.derive(sub_now).health
    assert health.events_dropped_subscribe == 3
    assert "fold_inputs_dropped" not in health.degraded
