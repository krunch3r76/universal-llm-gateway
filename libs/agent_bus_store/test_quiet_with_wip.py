"""Unit tests for A′ quiet-with-WIP pure predicate (architecture seven-test set)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_bus_store.quiet_with_wip import (
    DispatchLinkView,
    LaneTurnView,
    QuietWithWipSnapshot,
    evaluate_quiet_with_wip,
    seat_holding_execution_id,
)

NOW = datetime(2026, 8, 7, 1, 0, 0, tzinfo=UTC)
THRESHOLD_S = 300.0
SEAT = "web-anthropic"
# Specimen thread 22723 turn 6 closeout shape (+ exec= from turn 5 admit).
_SPECIMEN_22723_T6_DISPATCH = "fca8314b-4448-4f41-a0a2-2ad9976162d9"
_SPECIMEN_22723_T5_EXEC = "90063c9a-e374-44e7-8f56-a353670fd7cd"
_CLOSEOUT_SUBJECT_22723_T6 = (
    "CLOSEOUT|cursor-sdk CLOSEOUT "
    f"{_SPECIMEN_22723_T6_DISPATCH} exec={_SPECIMEN_22723_T5_EXEC} "
    "contract=mechanical caller=web-anthropic"
)


def _closeout_subject(exec_id: str, dispatch_id: str = "disp-close") -> str:
    return (
        f"CLOSEOUT|cursor-sdk CLOSEOUT {dispatch_id} exec={exec_id} "
        "contract=mechanical caller=web-anthropic"
    )


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


def _registration_body(execution_id: str) -> str:
    return (
        "TYPE: SEAT_REGISTRATION\n"
        "successor_birth_id: birth-1\n"
        "registration_id: reg-1\n"
        f"execution_id: {execution_id}\n"
        "parent_thread: T1\n"
    )


def test_seat_own_generate_alone_does_not_alarm() -> None:
    """The generate named on SEAT_REGISTRATION is the seat, not unharvested WIP.

    Breaks when a null-terminal link for that execution still yields
    wip_in_flight after the stamp is on the lane.
    """
    own = "eb3ea111-3615-4c77-906f-34ee4ce2ef1d"
    snap = _snap(
        links=(
            DispatchLinkView(
                execution_id=own,
                terminal_status=None,
                linked_at=_ts(30),
                pipeline_id="cdp-generate",
                liveness_witness="live",
            ),
        ),
        turns=(
            LaneTurnView(
                turn_number=1,
                from_agent=SEAT,
                created_at=_ts(2 * THRESHOLD_S),
                subject="earlier",
                body="seated",
            ),
            LaneTurnView(
                turn_number=2,
                from_agent="cursor-auto",
                created_at=_ts(THRESHOLD_S),
                subject="TYPE: SEAT_REGISTRATION — thread T1",
                body=_registration_body(own),
            ),
        ),
    )
    verdict = evaluate_quiet_with_wip(snap)
    assert verdict.fire is False
    assert verdict.skip_reason == "no_wip"
    assert verdict.wip_execution_ids == ()


def test_commissioned_dispatch_still_alarms_beside_own_generate() -> None:
    """Own generate is excluded; an in-flight commissioned link still fires.

    Breaks when the holder exclusion drops every nonterminal link, or when
    the commissioned id is omitted from wip_execution_ids.
    """
    own = "eb3ea111-3615-4c77-906f-34ee4ce2ef1d"
    commissioned = "exec-commissioned"
    snap = _snap(
        links=(
            DispatchLinkView(
                execution_id=own,
                terminal_status=None,
                linked_at=_ts(30),
                pipeline_id="cdp-generate",
                liveness_witness="live",
            ),
            DispatchLinkView(
                execution_id=commissioned,
                terminal_status=None,
                linked_at=_ts(30),
                pipeline_id="cursor-sdk-generate",
                liveness_witness="live",
            ),
        ),
        turns=(
            LaneTurnView(
                turn_number=1,
                from_agent=SEAT,
                created_at=_ts(2 * THRESHOLD_S),
                subject="commission",
                body="go",
            ),
            LaneTurnView(
                turn_number=2,
                from_agent="cursor-auto",
                created_at=_ts(THRESHOLD_S),
                subject="TYPE: SEAT_REGISTRATION — thread T1",
                body=_registration_body(own),
            ),
        ),
    )
    verdict = evaluate_quiet_with_wip(snap)
    assert verdict.fire is True
    assert verdict.reason == "wip_in_flight"
    assert verdict.wip_execution_ids == (commissioned,)


# Verbatim id lines from agent-bus 12286#3383 and 12286#3373.
_TURN_3383_ID_LINE = (
    "birth: successor_birth_id 0bde4a5a999f461b9674c07154fed381 · "
    "execution_id eb3ea111-3615-4c77-906f-34ee4ce2ef1d · configured "
    "claude-opus-5-5, hop target cdp/opus-5.5-extra. My generate turn on "
    "this lane: 12286#3382 (cdp generate — fbbc5dd77bb8, 20:59:28Z)."
)
_TURN_3373_ID_LINE = (
    "seat: successor_birth_id 17fffd18747c44e6813ba95bba404b83 · "
    "execution_id b488b712-5807-4b20-8422-b76d50d1825f · configured "
    "claude-opus-5-5 (serving model may differ) · hop target "
    "cdp/opus-5.5-extra · predecessor 01b6c866 (LEG 275)"
)


def test_seat_holding_id_from_turn_3383_inline_line() -> None:
    """12286#3383 names the holder mid-line, not as an execution_id: field.

    Breaks when the parser still requires the token at the start of a line.
    """
    turns = (
        LaneTurnView(
            turn_number=3383,
            from_agent=SEAT,
            created_at=_ts(THRESHOLD_S),
            subject="SEAT_REGISTRATION — successor 0bde4a5a (LEG 277)",
            body=f"TYPE: SEAT_REGISTRATION\n{_TURN_3383_ID_LINE}\n",
        ),
    )
    assert (
        seat_holding_execution_id(turns)
        == "eb3ea111-3615-4c77-906f-34ee4ce2ef1d"
    )


def test_seat_holding_id_from_turn_3373_inline_line() -> None:
    """12286#3373 names the holder on the seat: line, space-separated.

    Breaks when a predecessor registration's inline id is not extracted.
    """
    turns = (
        LaneTurnView(
            turn_number=3373,
            from_agent=SEAT,
            created_at=_ts(THRESHOLD_S),
            subject="SEAT_REGISTRATION — successor 17fffd18 (LEG 276)",
            body=f"TYPE: SEAT_REGISTRATION\n{_TURN_3373_ID_LINE}\n",
        ),
    )
    assert (
        seat_holding_execution_id(turns)
        == "b488b712-5807-4b20-8422-b76d50d1825f"
    )


def test_latest_stamp_without_execution_id_returns_none() -> None:
    """A newer stamp with no id hides an older stamp that has one.

    Breaks when the scan falls through to a predecessor registration.
    """
    turns = (
        LaneTurnView(
            turn_number=1,
            from_agent=SEAT,
            created_at=_ts(2 * THRESHOLD_S),
            subject="SEAT_REGISTRATION",
            body=f"TYPE: SEAT_REGISTRATION\n{_TURN_3373_ID_LINE}\n",
        ),
        LaneTurnView(
            turn_number=2,
            from_agent=SEAT,
            created_at=_ts(THRESHOLD_S),
            subject="SEAT_REGISTRATION",
            body="TYPE: SEAT_REGISTRATION\nverdict: seated\n",
        ),
    )
    assert seat_holding_execution_id(turns) is None


def test_refused_admit_specimen_22690_shape_skips_alarm() -> None:
    """AC1: frontier-admit pointer + FAILED| for link execution ⇒ no alarm debt."""
    term_at = _ts(THRESHOLD_S)
    snap = _snap(
        links=(
            DispatchLinkView(
                execution_id="exec-refused",
                terminal_status="failed",
                terminal_at=term_at,
                pipeline_id="cursor-sdk-generate",
            ),
        ),
        turns=(
            LaneTurnView(
                turn_number=1,
                from_agent="frontier-admit",
                created_at=_ts(THRESHOLD_S + 4),
                subject="cdp generate",
                body="admit",
            ),
            LaneTurnView(
                turn_number=2,
                from_agent="dispatch",
                created_at=_ts(THRESHOLD_S),
                subject=(
                    "FAILED|execution_id=exec-refused|dispatch_id=disp-1|"
                    "code=CURSOR_SOURCE_REF_IN_FLIGHT|in flight"
                ),
                body="refused",
            ),
            LaneTurnView(
                turn_number=3,
                from_agent="frontier-admit",
                created_at=_ts(2 * THRESHOLD_S),
                subject="commission",
                body="still waiting",
            ),
        ),
    )
    verdict = evaluate_quiet_with_wip(snap)
    assert verdict.fire is False
    assert verdict.skip_reason == "refused_admit"


def test_refused_link_dropped_inflight_peer_still_alarms() -> None:
    """A2: refused link must not suppress wip_in_flight on a sibling link."""
    term_at = _ts(THRESHOLD_S)
    snap = _snap(
        links=(
            DispatchLinkView(
                execution_id="exec-refused",
                terminal_status="failed",
                terminal_at=term_at,
                pipeline_id="cursor-sdk-generate",
            ),
            DispatchLinkView(
                execution_id="exec-live",
                terminal_status=None,
                linked_at=_ts(30),
                liveness_witness="live",
            ),
        ),
        turns=(
            LaneTurnView(
                turn_number=1,
                from_agent="frontier-admit",
                created_at=_ts(THRESHOLD_S + 30),
                subject="go",
                body="commission",
            ),
            LaneTurnView(
                turn_number=2,
                from_agent="cursor-sdk",
                created_at=_ts(THRESHOLD_S + 20),
                subject="execution_id=exec-live ack",
                body="running",
            ),
            LaneTurnView(
                turn_number=3,
                from_agent="dispatch",
                created_at=_ts(THRESHOLD_S),
                subject=(
                    "FAILED|execution_id=exec-refused|dispatch_id=disp-1|"
                    "code=CURSOR_SOURCE_REF_IN_FLIGHT|in flight"
                ),
                body="refused",
            ),
        ),
    )
    verdict = evaluate_quiet_with_wip(snap)
    assert verdict.fire is True
    assert verdict.reason == "wip_in_flight"
    assert verdict.wip_execution_ids == ("exec-live",)


def test_read_at_on_other_execution_does_not_harvest_link() -> None:
    """A3: mark_read on link B's closeout must not clear link A unharvested debt."""
    term_a = _ts(THRESHOLD_S)
    term_b = _ts(THRESHOLD_S / 2)
    snap = _snap(
        links=(
            DispatchLinkView(
                execution_id="exec-a",
                terminal_status="completed",
                terminal_at=term_a,
            ),
            DispatchLinkView(
                execution_id="exec-b",
                terminal_status="completed",
                terminal_at=term_b,
            ),
        ),
        turns=(
            LaneTurnView(
                turn_number=1,
                from_agent=SEAT,
                created_at=_ts(2 * THRESHOLD_S),
                subject="go",
                body="commission",
            ),
            LaneTurnView(
                turn_number=2,
                from_agent="cursor-sdk",
                created_at=_ts(THRESHOLD_S / 2 - 1),
                subject=_closeout_subject("exec-b"),
                body="status: complete",
                read_at=_ts(THRESHOLD_S / 2 - 1),
            ),
        ),
    )
    verdict = evaluate_quiet_with_wip(snap)
    assert verdict.fire is True
    assert verdict.reason == "closeout_unharvested"
    assert verdict.wip_execution_ids == ("exec-a",)


def test_refused_admit_plus_unharvested_peer_scoped_alarm() -> None:
    """B4: refused A + unharvested B ⇒ closeout_unharvested on exec-b only."""
    term_refused = _ts(THRESHOLD_S)
    term_b = _ts(THRESHOLD_S / 2)
    snap = _snap(
        links=(
            DispatchLinkView(
                execution_id="exec-a",
                terminal_status="failed",
                terminal_at=term_refused,
                pipeline_id="cursor-sdk-generate",
            ),
            DispatchLinkView(
                execution_id="exec-b",
                terminal_status="completed",
                terminal_at=term_b,
            ),
        ),
        turns=(
            LaneTurnView(
                turn_number=1,
                from_agent=SEAT,
                created_at=_ts(2 * THRESHOLD_S),
                subject="go",
                body="commission",
            ),
            LaneTurnView(
                turn_number=2,
                from_agent="dispatch",
                created_at=_ts(THRESHOLD_S),
                subject=(
                    "FAILED|execution_id=exec-a|dispatch_id=disp-a|"
                    "code=CURSOR_SOURCE_REF_IN_FLIGHT|in flight"
                ),
                body="refused",
            ),
        ),
    )
    verdict = evaluate_quiet_with_wip(snap)
    assert verdict.fire is True
    assert verdict.reason == "closeout_unharvested"
    assert verdict.wip_execution_ids == ("exec-b",)


def test_git_worker_draining_failed_not_refused_admit_still_alarms() -> None:
    """B3: GIT_WORKER_DRAINING is not a refused admit — debt must still alarm."""
    term_at = _ts(THRESHOLD_S)
    snap = _snap(
        links=(
            DispatchLinkView(
                execution_id="exec-drain",
                terminal_status="failed",
                terminal_at=term_at,
                pipeline_id="cursor-sdk-generate",
            ),
        ),
        turns=(
            LaneTurnView(
                turn_number=1,
                from_agent=SEAT,
                created_at=_ts(2 * THRESHOLD_S),
                subject="go",
                body="commission",
            ),
            LaneTurnView(
                turn_number=2,
                from_agent="dispatch",
                created_at=_ts(THRESHOLD_S),
                subject=(
                    "FAILED|execution_id=exec-drain|dispatch_id=disp-d|"
                    "code=GIT_WORKER_DRAINING|draining"
                ),
                body="drain",
            ),
        ),
    )
    verdict = evaluate_quiet_with_wip(snap)
    assert verdict.fire is True
    assert verdict.reason == "closeout_unharvested"
    assert verdict.wip_execution_ids == ("exec-drain",)


def test_closeout_read_at_counts_as_harvested_without_seat_turn() -> None:
    """AC2: worker closeout with read_at ⇒ no_wip; without read_at ⇒ unharvested alarm."""
    term_at = _ts(THRESHOLD_S)
    harvested_snap = _snap(
        links=(
            DispatchLinkView(
                execution_id=_SPECIMEN_22723_T5_EXEC,
                terminal_status="completed",
                terminal_at=term_at,
            ),
        ),
        turns=(
            LaneTurnView(
                turn_number=1,
                from_agent=SEAT,
                created_at=_ts(2 * THRESHOLD_S),
                subject="go",
                body="commission",
            ),
            LaneTurnView(
                turn_number=2,
                from_agent="cursor-sdk",
                created_at=_ts(THRESHOLD_S - 1),
                subject=_CLOSEOUT_SUBJECT_22723_T6,
                body="status: complete",
                read_at=_ts(THRESHOLD_S - 1),
            ),
        ),
    )
    assert evaluate_quiet_with_wip(harvested_snap).fire is False
    assert evaluate_quiet_with_wip(harvested_snap).skip_reason == "no_wip"

    unharvested_snap = _snap(
        links=(
            DispatchLinkView(
                execution_id="exec-read",
                terminal_status="completed",
                terminal_at=term_at,
            ),
        ),
        turns=(
            LaneTurnView(
                turn_number=1,
                from_agent=SEAT,
                created_at=_ts(2 * THRESHOLD_S),
                subject="go",
                body="commission",
            ),
            LaneTurnView(
                turn_number=2,
                from_agent="cursor-sdk",
                created_at=_ts(THRESHOLD_S - 1),
                subject=_closeout_subject("exec-read"),
                body="status: partial",
                read_at=None,
            ),
        ),
    )
    unread = evaluate_quiet_with_wip(unharvested_snap)
    assert unread.fire is True
    assert unread.reason == "closeout_unharvested"


def test_read_quiet_alarm_does_not_harvest_unread_closeout() -> None:
    """Seat mark_read on the quiet alarm must not clear unharvested closeout debt."""
    exec_id = "exec-unread-closeout"
    term_at = _ts(THRESHOLD_S)
    alarm_id = "qwa-testalarm01"
    snap = _snap(
        links=(
            DispatchLinkView(
                execution_id=exec_id,
                terminal_status="completed",
                terminal_at=term_at,
            ),
        ),
        turns=(
            LaneTurnView(
                turn_number=1,
                from_agent=SEAT,
                created_at=_ts(2 * THRESHOLD_S),
                subject="go",
                body="commission",
            ),
            LaneTurnView(
                turn_number=2,
                from_agent="cursor-sdk",
                created_at=_ts(THRESHOLD_S - 1),
                subject=_closeout_subject(exec_id),
                body="status: complete",
                read_at=None,
            ),
            LaneTurnView(
                turn_number=3,
                from_agent="dispatch",
                created_at=_ts(THRESHOLD_S / 2),
                subject="Quiet with work in flight",
                body=(
                    f"Quiet with work in flight — seat={SEAT} reason=closeout_unharvested "
                    f"execution_ids=[{exec_id}] alarm_id={alarm_id}. "
                    "Harvest or act; silence with WIP is the defect this turn names."
                ),
                read_at=_ts(THRESHOLD_S / 4),
            ),
        ),
    )
    verdict = evaluate_quiet_with_wip(snap)
    assert verdict.fire is True
    assert verdict.reason == "closeout_unharvested"
    assert verdict.wip_execution_ids == (exec_id,)


def test_seat_holding_id_accepts_equals_separator() -> None:
    """An execution_id= token mid-body is the holder id.

    Breaks when only colon or whitespace separators are recognized.
    """
    turns = (
        LaneTurnView(
            turn_number=1,
            from_agent=SEAT,
            created_at=_ts(THRESHOLD_S),
            subject="SEAT_REGISTRATION",
            body="TYPE: SEAT_REGISTRATION\nnote execution_id=exec-eq-1 trailing\n",
        ),
    )
    assert seat_holding_execution_id(turns) == "exec-eq-1"
