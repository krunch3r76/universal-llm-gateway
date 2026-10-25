"""Pure journal fold for mechanical-land runs (§4.3)."""

from __future__ import annotations

from mechanical_land.schema import (
    BlockedPayload,
    ClosedPayload,
    EvidenceHarvestedPayload,
    FlowForkedPayload,
    FlowJoinedPayload,
    GateRanPayload,
    GateResult,
    JournalEntry,
    JournalForkRefused,
    LandAttemptedPayload,
    LegAdmittedPayload,
    LegClosedPayload,
    LegRecord,
    ProbeRanPayload,
    RestartIssuedPayload,
    RestartPlannedPayload,
    ResumedPayload,
    ReviewAdmittedPayload,
    ReviewFallbackAdmittedPayload,
    ReviewVerdictPayload,
    RulingFoldedPayload,
    RulingIgnoredPayload,
    RunBirthedPayload,
    RunState,
    TransitionDecidedPayload,
    UnknownJournalEvent,
    parse_payload,
)


def fresh_state() -> RunState:
    """Return the folded tip before any journal events."""
    return RunState()


def fold(events: list[JournalEntry]) -> RunState:
    """Reduce an ordered journal into ``RunState``."""
    state = fresh_state()
    if not events:
        return state

    closed_dispatch_ids: set[str] = set()
    expected_prev = 0

    for entry in events:
        if entry.prev_seq != expected_prev:
            raise JournalForkRefused(
                f"prev_seq {entry.prev_seq} != expected {expected_prev} at seq {entry.seq}"
            )
        if entry.event not in _FOLD_HANDLERS:
            raise UnknownJournalEvent(entry.event)

        state.step = entry.step_id
        state.attempt = entry.attempt
        state.flow = entry.flow
        state.tip_seq = entry.seq

        if entry.event == "leg_closed":
            payload = parse_payload(entry.event, entry.payload)
            assert isinstance(payload, LegClosedPayload)
            if payload.dispatch_id in closed_dispatch_ids:
                expected_prev = entry.seq
                continue
            closed_dispatch_ids.add(payload.dispatch_id)

        _FOLD_HANDLERS[entry.event](state, entry)
        expected_prev = entry.seq

    return state


def _apply_run_birthed(state: RunState, entry: JournalEntry) -> None:
    payload = parse_payload(entry.event, entry.payload)
    assert isinstance(payload, RunBirthedPayload)
    state.kind = payload.kind
    state.source_ref = payload.source_ref
    state.policy_sha256 = payload.policy_sha256
    state.caller = payload.caller
    state.status = "running"


def _apply_policy_stamped(state: RunState, entry: JournalEntry) -> None:
    from mechanical_land.schema import PolicyStampedPayload

    payload = parse_payload(entry.event, entry.payload)
    assert isinstance(payload, PolicyStampedPayload)
    state.policy = payload.policy


def _apply_transition_decided(state: RunState, entry: JournalEntry) -> None:
    payload = parse_payload(entry.event, entry.payload)
    assert isinstance(payload, TransitionDecidedPayload)
    state.step = payload.to_step
    state.attempt = payload.attempt


def _apply_leg_admitted(state: RunState, entry: JournalEntry) -> None:
    payload = parse_payload(entry.event, entry.payload)
    assert isinstance(payload, LegAdmittedPayload)
    leg = LegRecord(
        step_id=payload.step_id,
        attempt=payload.attempt,
        flow=entry.flow,
        dispatch_id=payload.dispatch_id,
        execution_id=payload.execution_id,
        model=payload.model,
        contract=payload.contract,
        lane=payload.lane,
        sdk_mode=payload.sdk_mode,
        packet_sha256=payload.packet_sha256,
    )
    state.legs.setdefault(payload.step_id, []).append(leg)
    state.status = "awaiting_leg"


def _apply_leg_admit_failed(state: RunState, _entry: JournalEntry) -> None:
    state.status = "blocked"


def _apply_leg_closed(state: RunState, entry: JournalEntry) -> None:
    payload = parse_payload(entry.event, entry.payload)
    assert isinstance(payload, LegClosedPayload)
    state.last_closeout = payload
    legs = state.legs.get(entry.step_id, [])
    for leg in reversed(legs):
        if leg.dispatch_id == payload.dispatch_id:
            leg.closeout_status = payload.closeout_status
            leg.work_outcome = payload.work_outcome
            leg.head_sha = payload.head_sha
            leg.branch = payload.branch
            leg.branch_point = payload.branch_point
            leg.sidecar_ref = payload.sidecar_ref
            break
    else:
        leg = LegRecord(
            step_id=entry.step_id,
            attempt=entry.attempt,
            flow=entry.flow,
            dispatch_id=payload.dispatch_id,
            closeout_status=payload.closeout_status,
            work_outcome=payload.work_outcome,
            head_sha=payload.head_sha,
            branch=payload.branch,
            branch_point=payload.branch_point,
            sidecar_ref=payload.sidecar_ref,
        )
        state.legs.setdefault(entry.step_id, []).append(leg)


def _apply_gate_ran(state: RunState, entry: JournalEntry) -> None:
    payload = parse_payload(entry.event, entry.payload)
    assert isinstance(payload, GateRanPayload)
    ruff_exit = payload.ruff.exit if payload.ruff else None
    pytest_exit = payload.pytest.exit if payload.pytest else None
    state.gate_results[payload.attempt] = GateResult(
        attempt=payload.attempt,
        verdict=payload.verdict,
        head_sha=payload.head_sha,
        ruff_exit=ruff_exit,
        pytest_exit=pytest_exit,
    )


def _apply_review_admitted(state: RunState, entry: JournalEntry) -> None:
    payload = parse_payload(entry.event, entry.payload)
    assert isinstance(payload, ReviewAdmittedPayload)
    state.review.range = payload.range
    state.review.head = payload.head
    state.review.review_dispatch_id = payload.dispatch_id
    state.status = "awaiting_leg"


def _apply_review_verdict(state: RunState, _entry: JournalEntry) -> None:
    payload = parse_payload(_entry.event, _entry.payload)
    assert isinstance(payload, ReviewVerdictPayload)
    state.review.tip_sha = payload.tip_sha
    state.review.verdict = payload.verdict
    state.review.review_dispatch_id = payload.review_dispatch_id
    state.review.bind_status = payload.bind_status


def _apply_review_fallback_admitted(state: RunState, entry: JournalEntry) -> None:
    payload = parse_payload(entry.event, entry.payload)
    assert isinstance(payload, ReviewFallbackAdmittedPayload)
    state.review.fallback_model = payload.model


def _apply_land_attempted(state: RunState, _entry: JournalEntry) -> None:
    payload = parse_payload(_entry.event, _entry.payload)
    assert isinstance(payload, LandAttemptedPayload)
    state.land.tip_sha = payload.tip_sha
    state.land.before_sha = payload.before_sha
    state.land.after_sha = payload.after_sha
    state.land.landed = payload.landed
    state.land.refuse_reason = payload.refuse_reason


def _apply_restart_planned(state: RunState, _entry: JournalEntry) -> None:
    payload = parse_payload(_entry.event, _entry.payload)
    assert isinstance(payload, RestartPlannedPayload)
    state.restart.land_sha = payload.land_sha
    state.restart.restart_set = payload.restart_set
    state.restart.porcelain_block = payload.porcelain_block
    state.restart.unmapped = payload.unmapped


def _apply_restart_issued(state: RunState, _entry: JournalEntry) -> None:
    payload = parse_payload(_entry.event, _entry.payload)
    assert isinstance(payload, RestartIssuedPayload)
    state.restart.intents.append(
        {
            "service": payload.service,
            "intent_id": payload.intent_id,
            "state": payload.state,
        }
    )
    state.status = "awaiting_restart"


def _apply_probe_ran(state: RunState, _entry: JournalEntry) -> None:
    payload = parse_payload(_entry.event, _entry.payload)
    assert isinstance(payload, ProbeRanPayload)
    state.probe[payload.service] = {
        "observed_code_version": payload.observed_code_version,
        "live": payload.live,
    }


def _apply_closed(state: RunState, entry: JournalEntry) -> None:
    payload = parse_payload(entry.event, entry.payload)
    assert isinstance(payload, ClosedPayload)
    state.close.todo_close = payload.todo_close_result
    state.close.friction_close = tuple(payload.friction_close_result)
    state.status = "closed"


def _apply_evidence_harvested(state: RunState, _entry: JournalEntry) -> None:
    from mechanical_land.schema import EvidenceState

    payload = parse_payload(_entry.event, _entry.payload)
    assert isinstance(payload, EvidenceHarvestedPayload)
    state.evidence = EvidenceState(
        sidecar_ref=payload.sidecar_ref,
        sha256=payload.sha256,
        status=payload.status,
        excerpt_sha256=payload.excerpt_sha256,
    )


def _apply_ruling_folded(state: RunState, _entry: JournalEntry) -> None:
    payload = parse_payload(_entry.event, _entry.payload)
    assert isinstance(payload, RulingFoldedPayload)
    state.rulings.append(payload)
    if payload.verb == "HOLD":
        state.pending_hold = True
        state.status = "blocked_hold"
    elif payload.verb == "BLOCK":
        state.status = "blocked"
        state.blocked_reason = payload.reason or payload.verb
    elif payload.verb == "RESUME":
        state.pending_hold = False


def _apply_ruling_ignored(state: RunState, _entry: JournalEntry) -> None:
    payload = parse_payload(_entry.event, _entry.payload)
    assert isinstance(payload, RulingIgnoredPayload)
    state.rulings.append(payload)


def _apply_blocked(state: RunState, entry: JournalEntry) -> None:
    payload = parse_payload(entry.event, entry.payload)
    assert isinstance(payload, BlockedPayload)
    state.status = "blocked"
    state.blocked_reason = payload.reason_code
    state.step = payload.step_id
    state.attempt = payload.attempt


def _apply_resumed(state: RunState, _entry: JournalEntry) -> None:
    payload = parse_payload(_entry.event, _entry.payload)
    assert isinstance(payload, ResumedPayload)
    state.pending_hold = False
    if state.status == "blocked_hold":
        state.status = "running"


def _apply_flow_forked(state: RunState, entry: JournalEntry) -> None:
    payload = parse_payload(entry.event, entry.payload)
    assert isinstance(payload, FlowForkedPayload)
    state.active_flows.add(entry.flow)
    _ = payload.from_seq


def _apply_flow_joined(state: RunState, entry: JournalEntry) -> None:
    payload = parse_payload(entry.event, entry.payload)
    assert isinstance(payload, FlowJoinedPayload)
    state.flow = payload.survivor_flow
    state.active_flows = {payload.survivor_flow}


_FOLD_HANDLERS = {
    "run_birthed": _apply_run_birthed,
    "policy_stamped": _apply_policy_stamped,
    "transition_decided": _apply_transition_decided,
    "leg_admitted": _apply_leg_admitted,
    "leg_admit_failed": _apply_leg_admit_failed,
    "leg_closed": _apply_leg_closed,
    "gate_ran": _apply_gate_ran,
    "review_admitted": _apply_review_admitted,
    "review_verdict": _apply_review_verdict,
    "review_fallback_admitted": _apply_review_fallback_admitted,
    "land_attempted": _apply_land_attempted,
    "restart_planned": _apply_restart_planned,
    "restart_issued": _apply_restart_issued,
    "probe_ran": _apply_probe_ran,
    "closed": _apply_closed,
    "evidence_harvested": _apply_evidence_harvested,
    "ruling_folded": _apply_ruling_folded,
    "ruling_ignored": _apply_ruling_ignored,
    "blocked": _apply_blocked,
    "resumed": _apply_resumed,
    "flow_forked": _apply_flow_forked,
    "flow_joined": _apply_flow_joined,
}
