"""Pure transition table for mechanical-land (§6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mechanical_land.schema import (
    GateVerdict,
    ReviewVerdict,
    RunState,
)


@dataclass(frozen=True)
class Transition:
    command: str
    step_id: str | None = None
    attempt: int | None = None
    to_step: str | None = None
    reason: str | None = None
    needs_packet: bool = False
    blocked: bool = False
    reason_code: str | None = None
    detail: str | None = None
    review_route: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"command": self.command}
        if self.step_id is not None:
            out["step_id"] = self.step_id
        if self.attempt is not None:
            out["attempt"] = self.attempt
        if self.to_step is not None:
            out["to_step"] = self.to_step
        if self.reason is not None:
            out["reason"] = self.reason
        if self.needs_packet:
            out["needs_packet"] = True
        if self.blocked:
            out["blocked"] = True
        if self.reason_code is not None:
            out["reason_code"] = self.reason_code
        if self.detail is not None:
            out["detail"] = self.detail
        if self.review_route is not None:
            out["review_route"] = self.review_route
        return out


def _route_order(policy: dict[str, Any]) -> list[str]:
    review = policy.get("review") or {}
    order = review.get("route_order") or ["check_review"]
    return [str(x) for x in order]


def _gated_head(state: RunState, policy: dict[str, Any]) -> str | None:
    del policy
    for attempt in sorted(state.gate_results.keys(), reverse=True):
        gr = state.gate_results[attempt]
        if gr.verdict == GateVerdict.GREEN:
            return gr.head_sha
        if attempt in state.waived_gate_attempts and gr.verdict == GateVerdict.RED:
            return gr.head_sha
    if not state.gate_results:
        return None
    return state.gate_results[max(state.gate_results)].head_sha


def _latest_gate_attempt(state: RunState) -> int | None:
    if not state.gate_results:
        return None
    return max(state.gate_results)


def _latest_gate(state: RunState):
    att = _latest_gate_attempt(state)
    if att is None:
        return None
    return state.gate_results[att]


def _next_step_attempt(state: RunState, step_id: str) -> int:
    legs = state.legs.get(step_id, [])
    return len(legs) + 1


def _implement_closeout_ok(state: RunState) -> bool:
    lc = state.last_closeout
    if lc is None:
        return False
    if lc.closeout_status == "complete":
        return True
    return lc.closeout_status == "partial" and lc.work_outcome not in ("checks_failed",)


def _leg_failed_or_scope(state: RunState) -> Transition | None:
    lc = state.last_closeout
    if lc is None:
        return None
    if lc.closeout_status == "failed":
        return Transition(
            command="block",
            blocked=True,
            reason_code="leg_failed",
            step_id=state.step,
            attempt=state.attempt,
        )
    if lc.forbidden_git_ops:
        return Transition(
            command="block",
            blocked=True,
            reason_code="forbidden_git_op",
            step_id=state.step,
            attempt=state.attempt,
        )
    return None


def _ruling_overlay(state: RunState) -> Transition | None:
    if not state.rulings:
        return None
    last = state.rulings[-1]
    verb = getattr(last, "verb", None)
    if verb == "HOLD" or state.pending_hold:
        return Transition(command="wait", reason="blocked_hold")
    if verb == "BLOCK":
        return Transition(
            command="block",
            blocked=True,
            reason_code="ruling_block",
            detail=getattr(last, "reason", None),
        )
    return None


def transition(policy: dict[str, Any], state: RunState) -> Transition:
    """Decide the next reactor command from folded state and policy."""
    kind = state.kind or "implement"

    if state.status in ("closed", "blocked"):
        return Transition(command="noop", reason="terminal")

    ruling = _ruling_overlay(state)
    if ruling is not None:
        if ruling.command != "wait" or state.pending_hold:
            return ruling

    if kind == "evidence":
        return _transition_evidence(state)

    if state.status == "blocked_hold" or state.pending_hold:
        return Transition(command="wait", reason="blocked_hold")

    # Birth → first implement leg
    if state.step in (None, "run") and not state.legs.get("implement"):
        return Transition(
            command="admit",
            step_id="implement",
            attempt=1,
            to_step="implement",
            reason="birth",
        )

    step = state.step or "run"

    if step == "implement":
        fail = _leg_failed_or_scope(state)
        if fail:
            return fail
        if state.status == "awaiting_leg" and not _implement_closeout_ok(state):
            return Transition(command="wait", reason="awaiting_leg")
        if not state.legs.get("implement") and not _implement_closeout_ok(state):
            return Transition(command="wait", reason="awaiting_leg")
        if _implement_closeout_ok(state):
            att = _latest_gate_attempt(state) or 0
            return Transition(
                command="gate",
                step_id="gate",
                attempt=att + 1,
                to_step="gate",
                reason="implement closed",
            )
        return Transition(command="wait", reason="implement_incomplete")

    if step == "fix":
        fail = _leg_failed_or_scope(state)
        if fail:
            return fail
        if state.status == "awaiting_leg":
            return Transition(command="wait", reason="awaiting_leg")
        if _implement_closeout_ok(state):
            att = _latest_gate_attempt(state) or 0
            return Transition(
                command="gate",
                step_id="gate",
                attempt=att + 1,
                to_step="gate",
                reason="fix closed",
            )
        return Transition(command="wait", reason="fix_incomplete")

    if step == "gate":
        gr = _latest_gate(state)
        if gr is None:
            return Transition(command="wait", reason="gate_pending")
        att = gr.attempt
        if gr.verdict == GateVerdict.INFRA_ERROR:
            return Transition(
                command="block",
                blocked=True,
                reason_code="gate_infra",
                step_id="gate",
                attempt=att,
            )
        effective_green = (
            gr.verdict == GateVerdict.GREEN or att in state.waived_gate_attempts
        )
        if not effective_green:
            if state.gate_fix_used == 0:
                return Transition(
                    command="admit",
                    step_id="fix",
                    attempt=_next_step_attempt(state, "fix"),
                    to_step="fix",
                    reason="gate_red",
                    needs_packet=True,
                )
            return Transition(
                command="block",
                blocked=True,
                reason_code="gate_red_after_fix",
                step_id="gate",
                attempt=att,
            )
        review_att = len(state.review_history) + 1
        route = state.review.route or _route_order(policy)[0]
        return Transition(
            command="review",
            step_id="review",
            attempt=review_att,
            to_step="review",
            reason="gate green",
            needs_packet=True,
            review_route=route,
        )

    if step == "review":
        return _transition_review(policy, state)

    if step == "land":
        if state.land.landed is True:
            return Transition(
                command="restart",
                step_id="restart",
                attempt=1,
                to_step="restart",
                reason="landed",
            )
        if state.land.landed is False and state.land.refuse_reason:
            return Transition(
                command="block",
                blocked=True,
                reason_code=state.land.refuse_reason,
                step_id="land",
                attempt=state.attempt,
            )
        return Transition(command="land", step_id="land", attempt=1, to_step="land")

    if step == "restart":
        if state.status == "awaiting_restart":
            return Transition(command="wait", reason="awaiting_restart")
        if state.restart.intents and any(
            i.get("state") == "expired" for i in state.restart.intents
        ):
            return Transition(
                command="block",
                blocked=True,
                reason_code="restart_intent_expired",
                step_id="restart",
                attempt=state.attempt,
            )
        if state.restart.restart_set and not state.probe:
            return Transition(
                command="probe",
                step_id="probe",
                attempt=1,
                to_step="probe",
                reason="restart issued",
            )
        return Transition(command="wait", reason="restart_pending")

    if step == "probe":
        if state.probe and all(p.get("live") for p in state.probe.values()):
            return Transition(
                command="close",
                step_id="close",
                attempt=1,
                to_step="close",
                reason="live",
            )
        return Transition(
            command="block",
            blocked=True,
            reason_code="not_live",
            step_id="probe",
            attempt=state.attempt,
        )

    if step == "close":
        if state.status == "closed":
            return Transition(command="noop", reason="closed")
        return Transition(command="close", step_id="close", attempt=1)

    return Transition(command="noop", reason=f"unhandled step={step}")


def _transition_review(policy: dict[str, Any], state: RunState) -> Transition:
    gated = _gated_head(state, policy)
    route = state.review.route or _route_order(policy)[0]
    verdict = state.review.verdict
    tip = state.review.tip_sha or state.review.head

    if state.status == "awaiting_review":
        return Transition(command="wait", reason="awaiting_review")

    if verdict == ReviewVerdict.APPROVE and gated and tip == gated:
        return Transition(
            command="land",
            step_id="land",
            attempt=1,
            to_step="land",
            reason="review approve",
        )

    if verdict == ReviewVerdict.REQUEST_CHANGES:
        blockers = state.review.blockers or []
        if blockers and state.review_fix_used == 0:
            return Transition(
                command="admit",
                step_id="fix",
                attempt=_next_step_attempt(state, "fix"),
                to_step="fix",
                reason="review_request_changes",
                needs_packet=True,
            )
        if blockers:
            return Transition(
                command="block",
                blocked=True,
                reason_code="review_request_changes",
                step_id="review",
                attempt=state.attempt,
            )

    if verdict == ReviewVerdict.UNBOUND:
        order = _route_order(policy)
        try:
            idx = order.index(route)
        except ValueError:
            idx = -1
        if idx + 1 < len(order):
            nxt = order[idx + 1]
            att = state.attempt if state.attempt > 0 else 1
            return Transition(
                command="review",
                step_id="review",
                attempt=att,
                to_step="review",
                reason="review_route_advanced",
                needs_packet=True,
                review_route=nxt,
            )
        return Transition(
            command="block",
            blocked=True,
            reason_code="review_unbound",
            step_id="review",
            attempt=state.attempt,
        )

    if gated and tip and tip != gated:
        return Transition(
            command="block",
            blocked=True,
            reason_code="tip_drift",
            step_id="review",
            attempt=state.attempt,
        )

    if state.review_admitted_pending:
        return Transition(command="wait", reason="awaiting_review")

    review_att = len(state.review_history) + 1
    return Transition(
        command="review",
        step_id="review",
        attempt=review_att,
        to_step="review",
        reason="review_start",
        needs_packet=True,
        review_route=route,
    )


def _transition_evidence(state: RunState) -> Transition:
    if state.step in (None, "run") and not state.legs.get("evidence"):
        return Transition(
            command="admit",
            step_id="evidence",
            attempt=1,
            to_step="evidence",
            reason="birth",
            needs_packet=True,
        )
    if state.evidence and state.evidence.sidecar_ref:
        return Transition(
            command="close",
            step_id="harvest",
            attempt=1,
            to_step="close",
            reason="evidence harvested",
        )
    lc = state.last_closeout
    if lc and lc.files_modified:
        return Transition(
            command="block",
            blocked=True,
            reason_code="evidence_run_wrote",
        )
    return Transition(command="wait", reason="awaiting_evidence_leg")
