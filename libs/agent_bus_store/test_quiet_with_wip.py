"""Unit tests for A′ quiet-with-WIP pure predicate (architecture seven-test set)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_bus_store.quiet_with_wip import (
    DispatchLinkView,
    LaneTurnView,
    QuietWithWipSnapshot,
    evaluate_quiet_with_wip,
)

NOW = datetime(2026, 8, 7, 1, 0, 0, tzinfo=UTC)
THRESHOLD_S = 300.0
SEAT = "web-anthropic"


def _ts(delta_s: float) -> str:
    return (NOW - timedelta(seconds=delta_s)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _snap(**overrides) -> QuietWithWipSnapshot:
    base = dict(
        thread_id="T1",
        seat=SEAT,
        now=NOW,
        threshold_s=THRESHOLD_S,
        lifecycle="active",
        links=(),
        turns=(),
        licensed_park=False,
        alarm_open=False,
    )
    base.update(overrides)
    return QuietWithWipSnapshot(**base)


def test_fires_on_silent_path() -> None:
    """Constraint-2: fixture has no seat action after debt; reason=wip_in_flight."""
    snap = _snap(
        links=(
            DispatchLinkView(
                execution_id="exec-inflight",
                terminal_status=None,
                linked_at=_ts(30),
            ),
        ),
        turns=(
            LaneTurnView(
                turn_number=1,
                from_agent=SEAT,
                created_at=_ts(2 * THRESHOLD_S),
                subject="earlier work",
                body="done for now",
            ),
        ),
    )
    verdict = evaluate_quiet_with_wip(snap)
    assert verdict.fire is True
    assert verdict.reason == "wip_in_flight"
    assert verdict.wip_execution_ids == ("exec-inflight",)


def test_unharvested_closeout_fires() -> None:
    """Worker closeout after last seat turn ⇒ closeout_unharvested."""
    snap = _snap(
        links=(
            DispatchLinkView(
                execution_id="exec-done",
                terminal_status="completed",
                terminal_at=_ts(THRESHOLD_S),
            ),
        ),
        turns=(
            LaneTurnView(
                turn_number=1,
                from_agent=SEAT,
                created_at=_ts(2 * THRESHOLD_S),
                subject="commissioned",
                body="go",
            ),
            LaneTurnView(
                turn_number=2,
                from_agent="cursor-sdk",
                created_at=_ts(THRESHOLD_S),
                subject="cursor-sdk dispatch complete",
                body="status: complete",
            ),
        ),
    )
    verdict = evaluate_quiet_with_wip(snap)
    assert verdict.fire is True
    assert verdict.reason == "closeout_unharvested"
    assert verdict.wip_execution_ids == ("exec-done",)


def test_unbound_pickup_fires() -> None:
    """pickup: turn with no later cite ⇒ pickup_unbound (reuses B)."""
    snap = _snap(
        turns=(
            LaneTurnView(
                turn_number=1,
                from_agent=SEAT,
                created_at=_ts(2 * THRESHOLD_S),
                subject="ARCHITECTURE BIND",
                body="TYPE: ARCHITECTURE BIND\npickup: cursor-auto\nBind the gate.",
            ),
        ),
    )
    verdict = evaluate_quiet_with_wip(snap)
    assert verdict.fire is True
    assert verdict.reason == "pickup_unbound"
    assert verdict.unbound_turn_labels == ("t1: ARCHITECTURE BIND",)


def test_seat_park_licence_survives_later_nontype_turn() -> None:
    """CDP reply after TYPE: PARKED does not cancel the licence."""
    from agent_bus_store.quiet_with_wip import seat_park_licence

    turns = (
        LaneTurnView(
            turn_number=1,
            from_agent=SEAT,
            created_at=_ts(900),
            body="TYPE: PARKED\nwake: chat_delivery\n",
        ),
        LaneTurnView(
            turn_number=2,
            from_agent=SEAT,
            created_at=_ts(800),
            body="# CDP generate result\nexecution_id: abc\n",
        ),
    )
    assert seat_park_licence(turns, SEAT) is True


def test_seat_park_licence_cleared_by_later_type_line() -> None:
    from agent_bus_store.quiet_with_wip import seat_park_licence

    turns = (
        LaneTurnView(
            turn_number=1,
            from_agent=SEAT,
            created_at=_ts(900),
            body="TYPE: PARKED\n",
        ),
        LaneTurnView(
            turn_number=2,
            from_agent=SEAT,
            created_at=_ts(100),
            body="TYPE: DISPOSITION\nverdict: ratify\n",
        ),
    )
    assert seat_park_licence(turns, SEAT) is False


def test_licensed_park_suppresses() -> None:
    """Open wake_owed / licensed park ⇒ skip even with in-flight WIP."""
    snap = _snap(
        licensed_park=True,
        links=(DispatchLinkView(execution_id="exec-x", terminal_status=None),),
        turns=(
            LaneTurnView(
                turn_number=1,
                from_agent=SEAT,
                created_at=_ts(2 * THRESHOLD_S),
                subject="parked",
                body="TYPE: PARKED\nwake: Monitor",
            ),
        ),
    )
    verdict = evaluate_quiet_with_wip(snap)
    assert verdict.fire is False
    assert verdict.skip_reason == "licensed_park"


def test_harvested_lane_silent_is_fine() -> None:
    """Terminal + seat answered ⇒ skip (idle with no work owed is not a defect)."""
    snap = _snap(
        links=(
            DispatchLinkView(
                execution_id="exec-ok",
                terminal_status="completed",
                terminal_at=_ts(2 * THRESHOLD_S),
            ),
        ),
        turns=(
            LaneTurnView(
                turn_number=1,
                from_agent=SEAT,
                created_at=_ts(3 * THRESHOLD_S),
                subject="commission",
                body="go",
            ),
            LaneTurnView(
                turn_number=2,
                from_agent="cursor-sdk",
                created_at=_ts(2 * THRESHOLD_S),
                subject="cursor-sdk dispatch complete",
                body="done",
            ),
            LaneTurnView(
                turn_number=3,
                from_agent=SEAT,
                created_at=_ts(2 * THRESHOLD_S - 10),
                subject="harvested",
                body="thanks — next",
            ),
        ),
    )
    # Seat spoke after terminal but then went silent — still no WIP.
    verdict = evaluate_quiet_with_wip(snap)
    assert verdict.fire is False
    assert verdict.skip_reason == "no_wip"


def test_two_lanes_evaluate_independently() -> None:
    """N lanes ⇒ N independent verdicts (composition claim made testable)."""
    quiet_wip = _snap(
        thread_id="lane-a",
        links=(
            DispatchLinkView(
                execution_id="a1",
                terminal_status=None,
                linked_at=_ts(30),
            ),
        ),
        turns=(
            LaneTurnView(
                turn_number=1,
                from_agent=SEAT,
                created_at=_ts(2 * THRESHOLD_S),
                subject="a",
                body="x",
            ),
        ),
    )
    harvested = _snap(
        thread_id="lane-b",
        links=(
            DispatchLinkView(
                execution_id="b1",
                terminal_status="completed",
                terminal_at=_ts(2 * THRESHOLD_S),
            ),
        ),
        turns=(
            LaneTurnView(
                turn_number=1,
                from_agent=SEAT,
                created_at=_ts(THRESHOLD_S / 2),
                subject="harvest",
                body="ok",
            ),
        ),
    )
    v_a = evaluate_quiet_with_wip(quiet_wip)
    v_b = evaluate_quiet_with_wip(harvested)
    assert v_a.fire is True and v_a.reason == "wip_in_flight"
    assert v_b.fire is False and v_b.skip_reason == "no_wip"
    assert v_a.fire != v_b.fire


def test_dead_stream_without_terminal_write_is_not_wip_in_flight() -> None:
    """qwa emitter must not call a stale null-terminal link live WIP (a:36832)."""
    snap = _snap(
        links=(
            DispatchLinkView(
                execution_id="96f5f3f0-2867-477a-99e5-d79573d77d1e",
                terminal_status=None,
                linked_at=_ts(7200),
            ),
        ),
        turns=(
            LaneTurnView(
                turn_number=1,
                from_agent=SEAT,
                created_at=_ts(2 * THRESHOLD_S),
                subject="earlier",
                body="still here",
            ),
        ),
    )
    verdict = evaluate_quiet_with_wip(snap)
    assert verdict.fire is False
    assert verdict.reason is None
    assert verdict.skip_reason == "no_wip"
    assert verdict.wip_execution_ids == ()


def test_live_witness_past_grace_counts_as_wip_in_flight() -> None:
    """A grace-stale link with liveness_witness=live is wip_in_flight."""
    snap = _snap(
        links=(
            DispatchLinkView(
                execution_id="67aae3ee-ff0b-4e63-bfad-de87ceac2f93",
                terminal_status=None,
                linked_at=_ts(7200),
                liveness_witness="live",
            ),
        ),
        turns=(
            LaneTurnView(
                turn_number=1,
                from_agent=SEAT,
                created_at=_ts(2 * THRESHOLD_S),
                subject="earlier",
                body="still here",
            ),
        ),
    )
    verdict = evaluate_quiet_with_wip(snap)
    assert verdict.fire is True
    assert verdict.reason == "wip_in_flight"
    assert verdict.wip_execution_ids == ("67aae3ee-ff0b-4e63-bfad-de87ceac2f93",)
