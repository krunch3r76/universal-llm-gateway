"""Fold behaviour, stated as the negative space each fold must respect.

Most of these assert what the folds must *not* conclude. That is deliberate: the
expensive defects in a monitor are not missing rows, they are confidently wrong
rows -- silence read as failure, a window close read as a root close, a judgment
gap read as a dispatch.
"""

from __future__ import annotations

from scripts.model_manager.ui.dispatch_monitor.core import signals
from scripts.model_manager.ui.dispatch_monitor.core.model import Model
from scripts.model_manager.ui.dispatch_monitor.core.protocols import Event

from .conftest import replay


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


def test_readmit_clears_parked_from_prior_window_terminal() -> None:
    """A new admit binds a new worker — prior window terminals must not park it."""
    model = Model()
    model.apply(
        Event(
            "manage.charter.tick.admitted",
            1_000,
            {"root": "6171", "worker_thread": "6177"},
        )
    )
    model.apply(
        Event(
            "frontier.sdk.worker.progress",
            1_100,
            {
                "dispatch_id": "w6177",
                "thread_id": "6177",
                "resolved_model": "cursor/grok-4.6",
            },
        )
    )
    model.apply(
        Event(
            "frontier.sdk.worker.completed",
            1_200,
            {"dispatch_id": "w6177", "thread_id": "6177", "status": "completed"},
        )
    )
    parked = model.derive(10_000_000)
    assert _row(parked.roots, "root_id", "6171").state == "parked"

    model.apply(
        Event(
            "manage.charter.tick.admitted",
            1_300,
            {"root": "6171", "worker_thread": "6183"},
        )
    )
    frame = model.derive(10_000_000)
    root = _row(frame.roots, "root_id", "6171")
    assert root.state == "in_flight"
    assert root.worker_thread == "6183"
    assert not any(i.kind == "charter.root.parked" for i in frame.attention)


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
def test_giw_completed_keys_on_dispatch_id_not_execution_id() -> None:
    """GIW completed carries distinct dispatch_id + execution_id — one row, not two.

    Preferring execution_id left progress under dispatch_id live forever while the
    terminal landed on a sibling execution_id row (board: N live · N done).
    """
    model = Model()
    model.apply(
        Event(
            "frontier.sdk.worker.progress",
            1_000,
            {"dispatch_id": "efe9-sdk", "thread_id": "6160", "resolved_model": "cursor/opus-5"},
        )
    )
    model.apply(
        Event(
            "frontier.sdk.worker.completed",
            2_000,
            {
                "dispatch_id": "efe9-sdk",
                "execution_id": "37568616-run-uuid",
                "outcome": "ok",
                "thread_id": "6160",
            },
        )
    )
    frame = model.derive(3_000)
    assert len(frame.sdk) == 1
    row = _row(frame.sdk, "dispatch_id", "efe9-sdk")
    assert row.terminal_ms == 2_000
    assert row.state == "completed"


def test_stargate_queued_prefers_dispatch_id_not_execution_id() -> None:
    """Stargate-shaped queued (request_id, no source_repo) must key on dispatch_id."""
    model = Model()
    model.apply(
        Event(
            "frontier.sdk.worker.queued",
            1_000,
            {
                "request_id": "5ed13fdd78a1",
                "execution_id": "39369a01-0936",
                "dispatch_id": "5ed13fdd78a1-badcb93b",
                "queue_position": 2,
            },
        )
    )
    frame = model.derive(2_000)
    assert len(frame.sdk) == 1
    row = _row(frame.sdk, "dispatch_id", "5ed13fdd78a1-badcb93b")
    assert row.state == "queued"


def test_cancelled_terminals_row() -> None:
    """frontier.sdk.worker.cancelled is a lifecycle terminal."""
    model = Model()
    model.apply(
        Event(
            "frontier.sdk.worker.queued",
            1_000,
            {"dispatch_id": "ghost-exec", "queue_position": 1},
        )
    )
    model.apply(
        Event(
            "frontier.sdk.worker.cancelled",
            2_000,
            {
                "dispatch_id": "ghost-exec",
                "method": "queued_only",
                "reason": "operator backfill",
            },
        )
    )
    row = _row(model.derive(3_000).sdk, "dispatch_id", "ghost-exec")
    assert row.terminal_ms == 2_000
    assert row.state == "cancelled"
    assert "queued_only" in (row.failure_reason or "")


def test_completed_closes_execution_id_sibling_ghost() -> None:
    """Dual-id terminal merges the execution_id row into dispatch_id before close."""
    model = Model()
    model.apply(
        Event(
            "frontier.sdk.worker.queued",
            1_000,
            {
                "request_id": "rid",
                "execution_id": "exec-ghost",
            },
        )
    )
    model.apply(
        Event(
            "frontier.sdk.worker.progress",
            1_100,
            {"execution_id": "exec-ghost", "resolved_model": "cursor/grok-4.6"},
        )
    )
    model.apply(
        Event(
            "frontier.sdk.worker.progress",
            1_200,
            {"dispatch_id": "real-dispatch", "resolved_model": "cursor/grok-4.6"},
        )
    )
    model.apply(
        Event(
            "frontier.sdk.worker.completed",
            2_000,
            {
                "dispatch_id": "real-dispatch",
                "execution_id": "exec-ghost",
                "outcome": "ok",
            },
        )
    )
    frame = model.derive(3_000)
    assert len(frame.sdk) == 1
    primary = next(r for r in frame.sdk if r.dispatch_id == "real-dispatch")
    assert primary.terminal_ms == 2_000
    assert primary.state == "completed"
    assert not any(r.dispatch_id == "exec-ghost" for r in frame.sdk)


def test_toolcall_updates_last_tool_and_idle() -> None:
    """Last toolcall is ephemeral overlay; also resets idle via progress clock."""
    model = Model()
    model.apply(
        Event(
            "frontier.sdk.worker.progress",
            1_000,
            {"dispatch_id": "d1", "resolved_model": "cursor/grok-4.6"},
        )
    )
    model.apply(
        Event(
            "frontier.sdk.worker.toolcall",
            1_500,
            {"dispatch_id": "d1", "tool_name": "mcp", "status": "completed"},
        )
    )
    row = _row(model.derive(2_000).sdk, "dispatch_id", "d1")
    assert row.last_tool_name == "mcp"
    assert row.last_tool_status == "completed"
    assert row.last_progress_ms == 1_500


def test_progress_carries_live_tool_call_count() -> None:
    """worker.progress.tool_call_count is the live SDK row count (monotonic max)."""
    model = Model()
    model.apply(
        Event(
            "frontier.sdk.worker.progress",
            1_000,
            {
                "dispatch_id": "d-tc",
                "resolved_model": "cursor/grok-4.6",
                "tool_call_count": 3,
            },
        )
    )
    model.apply(
        Event(
            "frontier.sdk.worker.progress",
            1_500,
            {"dispatch_id": "d-tc", "tool_call_count": 7},
        )
    )
    model.apply(
        Event(
            "frontier.sdk.worker.progress",
            1_600,
            {"dispatch_id": "d-tc", "tool_call_count": 5},
        )
    )
    row = _row(model.derive(2_000).sdk, "dispatch_id", "d-tc")
    assert row.tool_call_count == 7
    model.apply(
        Event(
            "frontier.sdk.worker.completed",
            2_500,
            {
                "dispatch_id": "d-tc",
                "outcome": "ok",
                "tool_call_count": 9,
            },
        )
    )
    assert _row(model.derive(3_000).sdk, "dispatch_id", "d-tc").tool_call_count == 9


def test_toolcall_events_raise_live_tool_call_count() -> None:
    """Distinct worker.toolcall call_ids bump tc between 30s progress heartbeats."""
    model = Model()
    model.apply(
        Event(
            "frontier.sdk.worker.progress",
            1_000,
            {"dispatch_id": "d-live-tc", "tool_call_count": 40},
        )
    )
    model.apply(
        Event(
            "frontier.sdk.worker.toolcall",
            1_100,
            {
                "dispatch_id": "d-live-tc",
                "call_id": "c-a",
                "tool_name": "mcp",
                "status": "completed",
            },
        )
    )
    model.apply(
        Event(
            "frontier.sdk.worker.toolcall",
            1_200,
            {
                "dispatch_id": "d-live-tc",
                "call_id": "c-b",
                "tool_name": "Shell",
                "status": "completed",
            },
        )
    )
    # Duplicate call_id must not double-count (reconnect / redelivery).
    model.apply(
        Event(
            "frontier.sdk.worker.toolcall",
            1_250,
            {
                "dispatch_id": "d-live-tc",
                "call_id": "c-b",
                "tool_name": "Shell",
                "status": "completed",
            },
        )
    )
    row = _row(model.derive(1_300).sdk, "dispatch_id", "d-live-tc")
    assert row.tool_call_count == 42  # 40 floor + 2 distinct call_ids
    model.apply(
        Event(
            "frontier.sdk.worker.progress",
            1_400,
            {"dispatch_id": "d-live-tc", "tool_call_count": 45},
        )
    )
    assert _row(model.derive(1_500).sdk, "dispatch_id", "d-live-tc").tool_call_count == 45


def test_queued_and_generate_requested_carry_model() -> None:
    """Queued rows show model from queued.resolved_model or generate.requested."""
    model = Model()
    model.apply(
        Event(
            "frontier.sdk.generate.requested",
            900,
            {
                "request_id": "5ed13fdd78a1",
                "execution_id": "39369a01-0936",
                "resolved_model": "cursor/grok-4.6",
                "role": "cursor-sdk",
            },
        )
    )
    model.apply(
        Event(
            "frontier.sdk.worker.queued",
            1_000,
            {
                "dispatch_id": "5ed13fdd78a1-abc",
                "thread_id": "6170",
                "queue_position": 2,
                "resolved_model": "cursor/grok-4.6",
            },
        )
    )
    frame = model.derive(2_000)
    queued = _row(frame.sdk, "dispatch_id", "5ed13fdd78a1-abc")
    assert queued.state == "queued"
    assert queued.model == "cursor/grok-4.6"


def test_hold_paused_and_resumed_project_to_health() -> None:
    """tick.paused/held set hold; resumed clears — board paints from events."""
    model = Model()
    model.apply(
        Event(
            "manage.charter.tick.paused",
            1_000,
            {"reason": "admit storm", "set_by": "mcp", "set_at": 1.0},
        )
    )
    health = model.derive(2_000).health
    assert health.charter_hold is True
    assert health.charter_hold_reason == "admit storm"
    model.apply(Event("manage.charter.tick.resumed", 3_000, {"was_held": True}))
    health2 = model.derive(4_000).health
    assert health2.charter_hold is False
    assert health2.charter_hold_reason is None


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


def test_charter_telemetry_facade_signals_are_handled() -> None:
    """Kernel telemetry.py emitters must not flood signal.unhandled / DEGRADED."""
    model = Model()
    events = (
        ("manage.charter.tick.transition", {"root": "9001", "from_status": "idle", "to_status": "parked", "transition": "park"}),
        ("manage.charter.tick.shadow.diff", {"root": "9001", "old_decision": "skip", "kernel_transition": "admit", "classification": "disagree"}),
        ("manage.charter.tick.shadow.starved", {"reason": "empty_ledger", "bus_roots": 0}),
        ("manage.charter.tick.consult.queued", {"root": "9001", "gid": "g1", "role": "skeptic"}),
        ("manage.charter.tick.consult.deferred", {"root": "9001", "gid": "g1", "next_retry": 1.5}),
        ("manage.charter.tick.enrollment.filtered", {"root": "9001", "reason": "ledger_migrated"}),
        ("manage.charter.tick.frictions_audit_passed", {"root": "9001"}),
    )
    for i, (signal, payload) in enumerate(events, start=1):
        model.apply(Event(signal, i * 1_000, payload))
    health = model.derive(10_000).health
    assert health.unhandled_signals == {}
    assert "unhandled_signals" not in health.degraded


def test_implement_source_ref_unresolved_opens_row_and_attention() -> None:
    """GIW admission-time gate-bypass signal must not flood signal.unhandled."""
    model = Model()
    model.apply(
        Event(
            "frontier.sdk.implement.source_ref_unresolved",
            1_000,
            {
                "dispatch_id": "exec-gate-bypass",
                "thread_id": "6164",
                "execution_id": "exec-gate-bypass",
            },
        )
    )
    frame = model.derive(2_000)
    assert frame.health.unhandled_signals == {}
    row = next(r for r in frame.sdk if r.dispatch_id == "exec-gate-bypass")
    assert row.implement_gate_bypass is True
    assert row.contract == "implement"
    assert any(
        i.kind == "sdk.dispatch.implement_gate_bypass" for i in frame.attention
    )


def test_closeout_relayed_is_handled_and_stamps_row() -> None:
    """closeout.relayed must not flood unhandled_signals; stamps closeout_uri + identity."""
    model = Model()
    dispatch_id = "exec-relay-closeout"
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_000,
            {"dispatch_id": dispatch_id, "execution_id": dispatch_id},
        )
    )
    model.apply(
        Event(
            signals.SDK_CLOSEOUT_RELAYED,
            2_000,
            {
                "dispatch_id": dispatch_id,
                "execution_id": dispatch_id,
                "thread_id": "6592",
                "closeout_status": "complete",
                "receipt_path": "workspaces://universal-llm-gateway/tmp/reviews/closeouts/abc.md",
                "asked_by": "cursor-auto",
                "purpose": "operator-proxy",
                "story_id": "6563-w3",
            },
        )
    )
    frame = model.derive(3_000)
    assert frame.health.unhandled_signals == {}
    row = _row(frame.sdk, "dispatch_id", dispatch_id)
    assert row.closeout_uri == (
        "workspaces://universal-llm-gateway/tmp/reviews/closeouts/abc.md"
    )
    assert row.asked_by == "cursor-auto"
    assert row.purpose == "operator-proxy"
    assert row.story_id == "6563-w3"
    assert row.terminal_ms is None


def test_lease_released_after_worker_terminal_does_not_raise_attention() -> None:
    """Happy path: terminal then lease.released — no lease-without-terminal flag."""
    model = Model()
    dispatch_id = "exec-terminal-then-lease"
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_000,
            {"dispatch_id": dispatch_id, "execution_id": dispatch_id},
        )
    )
    model.apply(
        Event(
            signals.SDK_WORKER_FAILED,
            2_000,
            {
                "dispatch_id": dispatch_id,
                "execution_id": dispatch_id,
                "status": "failed",
                "failure_reason": "worker error",
            },
        )
    )
    model.apply(
        Event(
            signals.SDK_LEASE_RELEASED,
            3_000,
            {"dispatch_id": dispatch_id, "source_repo": "universal-llm-gateway"},
        )
    )
    frame = model.derive(4_000)
    row = _row(frame.sdk, "dispatch_id", dispatch_id)
    assert row.state != "running"
    assert row.terminal_ms is not None
    assert row.lease_released_without_terminal is False
    assert not any(
        i.kind == "sdk.dispatch.lease_released_without_terminal"
        for i in frame.attention
    )


def test_lease_released_without_terminal_raises_crit_attention() -> None:
    """Zombie path: lease.released with no worker terminal — flag + attention, no infer."""
    model = Model()
    dispatch_id = "exec-lease-no-terminal"
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
    frame = model.derive(3_000)
    assert frame.health.unhandled_signals == {}
    row = _row(frame.sdk, "dispatch_id", dispatch_id)
    assert row.lease_released_without_terminal is True
    assert row.terminal_ms is None
    assert row.state != "completed"
    assert row.state != "failed"
    assert any(
        i.kind == "sdk.dispatch.lease_released_without_terminal"
        and i.severity == "crit"
        for i in frame.attention
    )


def test_lease_released_during_park_does_not_raise_attention() -> None:
    """Intentional nest park: parked_waiting parent must not get lease-without-terminal."""
    model = Model()
    parent_id = "exec-park-parent-lease"
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_000,
            {"dispatch_id": parent_id, "execution_id": parent_id},
        )
    )
    model.apply(
        Event(
            signals.SDK_LEASE_PARK_ENTER,
            2_000,
            {"parent_id": parent_id, "child_id": "exec-park-child"},
        )
    )
    model.apply(
        Event(
            signals.SDK_LEASE_RELEASED,
            3_000,
            {"dispatch_id": parent_id, "source_repo": "universal-llm-gateway"},
        )
    )
    frame = model.derive(4_000)
    row = _row(frame.sdk, "dispatch_id", parent_id)
    assert row.state == "parked_waiting"
    assert row.lease_released_without_terminal is False
    assert not any(
        i.kind == "sdk.dispatch.lease_released_without_terminal"
        for i in frame.attention
    )


def test_write_lease_acquired_then_released_clears_health_holder() -> None:
    """GIW lease paint clears when lease.released follows lease.acquired."""
    model = Model()
    dispatch_id = "exec-lease-acq-rel"
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_000,
            {"dispatch_id": dispatch_id, "execution_id": dispatch_id},
        )
    )
    model.apply(
        Event(
            signals.SDK_LEASE_ACQUIRED,
            1_500,
            {"dispatch_id": dispatch_id, "execution_id": dispatch_id},
        )
    )
    assert model.derive(2_000).health.lease_holder == dispatch_id
    model.apply(
        Event(
            signals.SDK_LEASE_RELEASED,
            2_500,
            {"dispatch_id": dispatch_id, "source_repo": "universal-llm-gateway"},
        )
    )
    assert model.derive(3_000).health.lease_holder is None


def test_write_lease_terminal_without_release_clears_health_holder() -> None:
    """Terminal holder row ⇒ no lease paint even when released never arrived."""
    model = Model()
    dispatch_id = "exec-lease-terminal-only"
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_000,
            {"dispatch_id": dispatch_id, "execution_id": dispatch_id},
        )
    )
    model.apply(
        Event(
            signals.SDK_LEASE_ACQUIRED,
            1_500,
            {"dispatch_id": dispatch_id, "execution_id": dispatch_id},
        )
    )
    assert model.derive(2_000).health.lease_holder == dispatch_id
    model.apply(
        Event(
            signals.SDK_WORKER_FAILED,
            3_000,
            {
                "dispatch_id": dispatch_id,
                "execution_id": dispatch_id,
                "status": "failed",
            },
        )
    )
    assert model.derive(4_000).health.lease_holder is None


def test_charter_scanned_manage_does_not_paint_giw_lease_holder() -> None:
    """Tick ``lease_holder=manage`` must not appear on the GIW write-lease strip."""
    model = Model()
    model.apply(
        Event(
            signals.CHARTER_SCANNED,
            1_000,
            {"roots": 0, "admitted": 0, "lease_holder": "manage"},
        )
    )
    assert model.derive(2_000).health.lease_holder is None
    assert model.charter.lease_holder == "manage"


def test_park_enter_paints_child_as_write_lease_holder() -> None:
    """Nested park yields the write-lease to the live child dispatch."""
    model = Model()
    parent_id = "exec-park-parent-holder"
    child_id = "exec-park-child-holder"
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_000,
            {"dispatch_id": parent_id, "execution_id": parent_id},
        )
    )
    model.apply(
        Event(
            signals.SDK_LEASE_ACQUIRED,
            1_200,
            {"dispatch_id": parent_id, "execution_id": parent_id},
        )
    )
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_500,
            {"dispatch_id": child_id, "execution_id": child_id},
        )
    )
    model.apply(
        Event(
            signals.SDK_LEASE_PARK_ENTER,
            2_000,
            {"parent_id": parent_id, "child_id": child_id},
        )
    )
    frame = model.derive(3_000)
    assert frame.health.lease_holder == child_id


def test_shadow_diff_does_not_mint_unknown_root_rows() -> None:
    """shadow.diff is high-volume Phase-1 noise — swallow without creating ACTIVE unknowns."""
    model = Model()
    for i, root in enumerate(("5975", "5993", "6153"), start=1):
        model.apply(
            Event(
                "manage.charter.tick.shadow.diff",
                i * 1_000,
                {
                    "root": root,
                    "old_decision": "skip",
                    "kernel_transition": "noop",
                    "classification": "agree",
                },
            )
        )
    frame = model.derive(10_000)
    assert frame.roots == ()
    assert frame.health.unhandled_signals == {}


def test_transition_and_objective_do_not_mint_unknown_root_rows() -> None:
    """Post-close telemetry must not resurrect roots aged out of the seed window."""
    model = Model()
    for i, (signal, payload) in enumerate(
        (
            (
                "manage.charter.tick.transition",
                {"root": "5975", "from_status": "closed", "to_status": "closed"},
            ),
            (
                "monitor.meta.charter_objective",
                {"root": "6171", "objective": "Standing fleet conveyor"},
            ),
        ),
        start=1,
    ):
        model.apply(Event(signal, i * 1_000, payload))
    frame = model.derive(5_000)
    assert frame.roots == ()


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


def test_admitted_via_first_writer_wins() -> None:
    """Later sparse events must not blank first ``admitted_via`` / ``asked_by``."""
    model = Model()
    model.apply(
        Event(
            signals.SDK_WORKER_QUEUED,
            1_000,
            {
                "dispatch_id": "auto-nest1",
                "thread_id": "5867",
                "execution_id": "exec-auto-nest1",
                "admitted_via": "cursor-auto",
                "asked_by": "web-anthropic",
            },
        )
    )
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            2_000,
            {
                "dispatch_id": "auto-nest1",
                "thread_id": "5867",
                "execution_id": "exec-auto-nest1",
                "seat": "cursor-sdk",
            },
        )
    )
    row = _row(model.derive(3_000).sdk, "dispatch_id", "auto-nest1")
    assert row.admitted_via == "cursor-auto"
    assert row.asked_by == "web-anthropic"
    assert row.seat == "cursor-sdk"


def test_queued_replay_after_start_does_not_revert_running() -> None:
    """F2: replayed ``worker.queued`` after start must not clear ``started_ms``."""
    model = Model()
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_000,
            {
                "dispatch_id": "auto-nest2",
                "thread_id": "5867",
                "execution_id": "exec-auto-nest2",
                "seat": "cursor-sdk",
                "admitted_via": "cursor-auto",
                "asked_by": "cursor",
            },
        )
    )
    model.apply(
        Event(
            signals.SDK_WORKER_QUEUED,
            2_000,
            {
                "dispatch_id": "auto-nest2",
                "thread_id": "5867",
                "execution_id": "exec-auto-nest2",
                "admitted_via": "cursor-auto",
                "asked_by": "web-anthropic",
            },
        )
    )
    row = _row(model.derive(3_000).sdk, "dispatch_id", "auto-nest2")
    assert row.state == "running"
    assert row.started_ms == 1_000
    assert row.admitted_via == "cursor-auto"
    assert row.asked_by == "cursor"


def test_worker_dispatched_maps_emitter_worker_only() -> None:
    """AC6: ``SDK_WORKER_DISPATCHED`` → worker lane; pin worker-only fixture."""
    model = Model()
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_000,
            {
                "dispatch_id": "auto-emit1",
                "thread_id": "5867",
                "execution_id": "exec-auto-emit1",
                "seat": "cursor-sdk",
                "admitted_via": "cursor-auto",
                "asked_by": "web-anthropic",
            },
        )
    )
    model.apply(
        Event(
            signals.SDK_WORKER_COMPLETED,
            2_000,
            {
                "dispatch_id": "auto-emit1",
                "thread_id": "5867",
                "execution_id": "exec-auto-emit1",
                "outcome": "ok",
            },
        )
    )
    row = _row(model.derive(3_000).sdk, "dispatch_id", "auto-emit1")
    assert row.emitters_seen == ("worker",)


def test_signature_pairs_distinct_by_admitted_via_and_asked_by() -> None:
    """Three fixture pairs yield pairwise-distinct (admitted_via, asked_by)."""
    model = Model()
    fixtures = (
        ("auto-a1", "cursor-auto", "web-anthropic"),
        ("auto-a2", "cursor-auto", "cursor"),
        ("stg-a1", "stargate", "cursor"),
    )
    for i, (disp, via, asked) in enumerate(fixtures):
        model.apply(
            Event(
                signals.SDK_WORKER_DISPATCHED,
                (i + 1) * 1_000,
                {
                    "dispatch_id": disp,
                    "thread_id": "5867",
                    "execution_id": f"exec-{disp}",
                    "seat": "cursor-sdk",
                    "admitted_via": via,
                    "asked_by": asked,
                },
            )
        )
    frame = model.derive(10_000)
    pairs = {
        (row.admitted_via, row.asked_by)
        for row in frame.sdk
        if row.dispatch_id.startswith(("auto-a", "stg-a"))
    }
    assert pairs == {
        ("cursor-auto", "web-anthropic"),
        ("cursor-auto", "cursor"),
        ("stargate", "cursor"),
    }
    assert all(row.seat == "cursor-sdk" for row in frame.sdk if row.seat)


def test_identical_work_refire_refused_surfaces_stuck_root() -> None:
    model = Model()
    model.apply(
        Event(
            signals.CHARTER_CONSULT_QUEUED,
            1_000,
            {"root": "6563", "gid": "G3", "role": "judgment_gap"},
        )
    )
    model.apply(
        Event(
            signals.CHARTER_IDENTICAL_WORK_REFIRE_REFUSED,
            2_000,
            {
                "root": "6563",
                "work_key": "abc123",
                "friction_id": 27259,
            },
        )
    )
    root = _row(model.derive(3_000).roots, "root_id", "6563")
    assert root.state == "stuck"
    assert "identical_work_refire" in (root.skip_reason or "")


def test_consult_queued_streak_surfaces_stuck_after_n_scans() -> None:
    model = Model()
    model.apply(
        Event(
            signals.CHARTER_CONSULT_QUEUED,
            1_000,
            {"root": "6563", "gid": "G3"},
        )
    )
    for offset, ts in enumerate((2_000, 3_000, 4_000), start=1):
        model.apply(Event(signals.CHARTER_SCANNED, ts, {"roots": 1, "admitted": 0}))
        root = _row(model.derive(ts).roots, "root_id", "6563")
        if offset < 3:
            assert root.state == "consult_queued"
        else:
            assert root.state == "stuck"
            assert root.skip_reason == "consult_queued_streak"
