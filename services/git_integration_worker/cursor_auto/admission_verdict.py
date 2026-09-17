"""Body-pure prefix of the admit-gate ladder, plus its wire projection.

``blocking_admit_gate`` is the authority for whether a job is admitted, and it
runs inside ``process_job`` — after ``POST /enqueue`` has already answered the
caller. This module owns the ordered prefix of that ladder that needs no I/O, so
``/enqueue`` can report a verdict about *this job* instead of the Auto handler's
heartbeat.

One authority, two callers. ``blocking_admit_gate`` delegates its body-pure
prefix here and keeps sole ownership of the terminal post and the emits;
``/enqueue`` renders the same verdict as a projection that names the gates it
covers. The four thread-state gates need ``fetch_thread_status`` /
``fetch_thread_turns`` and so never evaluate here — they are reported
``deferred``, never ``admitted``. ``agent_bus_read(job_state)`` remains the
authority-of-record and the recovery path.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from claim_register import claimed_derived

from services.git_integration_worker.cursor_auto.directive import (
    NESTED_SCOPE_CONTRACTS,
    VISION_REQUIRED_CONTRACTS,
    body_has_contract_override,
    empty_directive_missed_tokens,
    has_actionable_scope,
    has_vision_field,
    is_continuity_hop_request,
    parse_request_body,
)
from services.git_integration_worker.cursor_auto.dispatch_bounds import (
    scope_waiver_allowed,
)
from services.git_integration_worker.cursor_auto.execute_admission import (
    EXECUTE_CONTRACT,
    ExecuteAdmission,
    admit_execute_body,
)
from services.git_integration_worker.cursor_auto.fix_hints import (
    CONTINUITY_HOP_FIX_HINT,
    EMPTY_SCOPE_FIX_HINT,
    MISSION_CLOSE_WAKE_FIX_HINT,
    OPTIONS_SYMMETRY_FIX_HINT,
    PROPAGATE_MISSING_FIX_HINT,
    VISION_MISSING_FIX_HINT,
)
from services.git_integration_worker.cursor_auto.options_admission import (
    admit_options_body,
)
from services.git_integration_worker.cursor_auto.propagate_admission import (
    PROPAGATE_CONTRACT,
    PropagateAdmission,
    admit_propagate_body,
)
from services.git_integration_worker.cursor_auto.wire_map import resolve_desired_model

#: Ladder prefix evaluable from ``subject`` + ``body`` + ``contract`` alone.
BODY_PURE_GATES: tuple[str, ...] = (
    "continuity_hop_misroute",
    "mission_close_wake",
    "pickup_awaits",
    "execute_admission",
    "propagate_admission",
    "empty_directive_scope",
    "vision_field_missing",
    "options_symmetry",
)

#: Ladder suffix requiring thread reads; never asserted synchronously.
THREAD_STATE_GATES: tuple[str, ...] = (
    "thread_terminal_status",
    "relay_trust",
    "synthesized_closeout_ack",
    "auth_gate_budget",
)

OUTCOME_REFUSED = "refused"
OUTCOME_DEFERRED = "deferred"
OUTCOME_WAIVED = "waived"
OUTCOME_ADMITTED = "admitted"
OUTCOME_NOT_APPLICABLE = "not_applicable"

#: Named authority for the projection — never the response field itself.
ADMISSION_AUTHORITY = "cursor_auto.admit_gates.blocking_admit_gate"
#: Authority-of-record when the synchronous projection is absent or stale.
ADMISSION_RECOVERY = "agent_bus_read(job_state)"

_ADMIT_GATE_CONTRACTS: frozenset[str] = frozenset({"confer", "ask"})


def admit_gate_entered(*, directive_present: bool, contract: str) -> bool:
    """True when ``process_job`` runs the admit ladder for this job at all.

    Mirrors the guard in ``process_job``; both callers read it from here so the
    projection cannot claim coverage the handler never attempts.
    """
    return (
        directive_present
        or contract in (NESTED_SCOPE_CONTRACTS | _ADMIT_GATE_CONTRACTS)
        or contract in {EXECUTE_CONTRACT, PROPAGATE_CONTRACT}
    )


@dataclass(frozen=True, slots=True)
class PendingEmit:
    """An advisory emit the pure evaluator names but does not fire."""

    name: str
    kwargs: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GateRefusal:
    """A body-pure gate's refusal — the payload ``_blocked`` will terminalize."""

    gate: str
    reason: str
    summary: str
    payload: dict[str, Any]
    journal_extra: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class BodyPureVerdict:
    """Verdict of the ladder prefix, with the coverage it actually spans."""

    outcome: str
    refusal: GateRefusal | None = None
    asserted: tuple[str, ...] = ()
    deferred: tuple[str, ...] = ()
    not_applicable: tuple[str, ...] = ()
    waived: tuple[str, ...] = ()
    execute_admission: ExecuteAdmission | None = None
    propagate_admission: PropagateAdmission | None = None
    pending_emits: tuple[PendingEmit, ...] = ()


class _Ladder:
    """Accumulates per-gate coverage while the prefix walks in ladder order."""

    def __init__(self) -> None:
        self.asserted: list[str] = []
        self.waived: list[str] = []
        self.emits: list[PendingEmit] = []

    def ran(self, gate: str) -> None:
        self.asserted.append(gate)

    def skipped_after(self, gate: str) -> tuple[str, ...]:
        """Body-pure gates that never evaluate once ``gate`` short-circuits."""
        cut = BODY_PURE_GATES.index(gate) + 1
        return BODY_PURE_GATES[cut:]

    def refuse(self, refusal: GateRefusal) -> BodyPureVerdict:
        self.ran(refusal.gate)
        return BodyPureVerdict(
            outcome=OUTCOME_REFUSED,
            refusal=refusal,
            asserted=tuple(self.asserted),
            not_applicable=self.skipped_after(refusal.gate),
            waived=tuple(self.waived),
            pending_emits=tuple(self.emits),
        )


def evaluate_body_pure_gates(
    *,
    subject: str,
    body: str,
    contract: str,
    desired_model: str,
    continuity_hop: bool = False,
) -> BodyPureVerdict:
    """Walk the admit ladder as far as zero-I/O predicates carry it.

    Fires nothing: refusal payloads and advisory emits are returned as data so
    the async caller stays the only site that terminalizes or emits.
    """
    from claude_bundles.mission_close_wake import validate_mission_close_wake
    from claude_bundles.pickup_awaits import (
        PICKUP_AWAITS_STOP_FIX_HINT,
        PICKUP_DECLARATION_FIX_HINT,
        validate_pickup_awaits,
    )

    ladder = _Ladder()
    contract = (contract or "answer").strip().lower()

    is_hop, _token = is_continuity_hop_request(body, wire_flag=bool(continuity_hop))
    if is_hop or continuity_hop:
        summary = (
            "Continuity hop reached implement admit — routing defect "
            "(continuity_hop_misroute). Do not add vision/scope fields."
        )
        return ladder.refuse(
            GateRefusal(
                gate="continuity_hop_misroute",
                reason="continuity_hop_misroute",
                summary=summary,
                payload={
                    "summary": summary,
                    "reason": "continuity_hop_misroute",
                    "fix_hint": CONTINUITY_HOP_FIX_HINT,
                },
            )
        )
    ladder.ran("continuity_hop_misroute")

    wake = validate_mission_close_wake(subject=subject or "", body=body or "")
    if not wake.ok:
        reason = wake.reason or "mission_close_wake_path_missing"
        summary = (
            "Mission close refused — outstanding work has no named wake path "
            f"({reason})."
        )
        return ladder.refuse(
            GateRefusal(
                gate="mission_close_wake",
                reason=reason,
                summary=summary,
                payload={
                    "summary": summary,
                    "reason": reason,
                    "missed_tokens": list(wake.missed_tokens),
                    "fix_hint": MISSION_CLOSE_WAKE_FIX_HINT,
                },
            )
        )
    ladder.ran("mission_close_wake")

    pickup = validate_pickup_awaits(
        subject=subject or "",
        body=body or "",
        prior_turns=None,
    )
    if not pickup.ok:
        reason = pickup.reason or "pickup_declaration_missing"
        summary = f"Pickup/awaits gate refused ({reason})."
        hint = (
            PICKUP_AWAITS_STOP_FIX_HINT
            if reason == "pickup_awaits_unbound"
            else PICKUP_DECLARATION_FIX_HINT
        )
        return ladder.refuse(
            GateRefusal(
                gate="pickup_awaits",
                reason=reason,
                summary=summary,
                payload={
                    "summary": summary,
                    "reason": reason,
                    "missed_tokens": list(pickup.missed_tokens),
                    "fix_hint": hint,
                },
            )
        )
    ladder.ran("pickup_awaits")

    if contract == EXECUTE_CONTRACT:
        return _execute_gate(ladder, body=body, contract=contract)
    if contract == PROPAGATE_CONTRACT:
        return _propagate_gate(ladder, body=body, contract=contract)

    directive = parse_request_body(body)
    scope_verdict = _empty_scope_gate(
        ladder,
        body=body,
        contract=contract,
        desired_model=desired_model,
        directive=directive,
    )
    if scope_verdict is not None:
        return scope_verdict

    vision_verdict = _vision_gates(
        ladder, body=body, contract=contract, directive=directive
    )
    if vision_verdict is not None:
        return vision_verdict

    return BodyPureVerdict(
        outcome=OUTCOME_WAIVED if ladder.waived else OUTCOME_DEFERRED,
        asserted=tuple(ladder.asserted),
        deferred=THREAD_STATE_GATES,
        not_applicable=tuple(
            g for g in BODY_PURE_GATES if g not in set(ladder.asserted)
        ),
        waived=tuple(ladder.waived),
        pending_emits=tuple(ladder.emits),
    )


def _execute_gate(ladder: _Ladder, *, body: str, contract: str) -> BodyPureVerdict:
    """``contract: execute`` — approval short-circuits the whole ladder."""
    admission = admit_execute_body(body)
    if admission.approved:
        ladder.ran("execute_admission")
        return BodyPureVerdict(
            outcome=OUTCOME_ADMITTED,
            asserted=tuple(ladder.asserted),
            not_applicable=ladder.skipped_after("execute_admission"),
            waived=tuple(ladder.waived),
            execute_admission=admission,
            pending_emits=tuple(ladder.emits),
        )
    error = admission.error or {"reason": "execute_admission_refused"}
    summary = str(error.get("summary", "execute admission refused"))
    reason = str(error.get("reason", "execute_admission_refused"))
    ladder.emits.append(
        PendingEmit(
            name="execute_admission_blocked",
            kwargs={"reason": reason, "tool_op": error.get("tool_op")},
        )
    )
    verdict = ladder.refuse(
        GateRefusal(
            gate="execute_admission",
            reason=reason,
            summary=summary,
            payload={**error, "summary": summary, "contract": contract},
        )
    )
    return replace(verdict, execute_admission=admission)


def _propagate_gate(ladder: _Ladder, *, body: str, contract: str) -> BodyPureVerdict:
    """``contract: propagate`` — approval short-circuits the whole ladder."""
    admission = admit_propagate_body(body)
    if admission.approved:
        ladder.ran("propagate_admission")
        return BodyPureVerdict(
            outcome=OUTCOME_ADMITTED,
            asserted=tuple(ladder.asserted),
            not_applicable=ladder.skipped_after("propagate_admission"),
            waived=tuple(ladder.waived),
            propagate_admission=admission,
            pending_emits=tuple(ladder.emits),
        )
    error = admission.error or {"reason": "propagate_admission_refused"}
    summary = str(error.get("summary", "propagate admission refused"))
    verdict = ladder.refuse(
        GateRefusal(
            gate="propagate_admission",
            reason=str(error.get("reason", "propagate_admission_refused")),
            summary=summary,
            payload={
                **error,
                "summary": summary,
                "contract": contract,
                "fix_hint": error.get("fix_hint", PROPAGATE_MISSING_FIX_HINT),
            },
        )
    )
    return replace(verdict, propagate_admission=admission)


def _empty_scope_gate(
    ladder: _Ladder,
    *,
    body: str,
    contract: str,
    desired_model: str,
    directive: Any,
) -> BodyPureVerdict | None:
    """``empty_directive_scope`` — refuse, waive, or fall through."""
    if contract not in NESTED_SCOPE_CONTRACTS or has_actionable_scope(body):
        if contract in NESTED_SCOPE_CONTRACTS:
            ladder.ran("empty_directive_scope")
        return None
    density = directive.density if directive is not None else None
    resolved_model_id = str(
        resolve_desired_model(desired_model, contract=contract).get("resolved_model_id")
        or ""
    )
    override = body_has_contract_override(body)
    if override and scope_waiver_allowed(resolved_model_id):
        ladder.emits.append(
            PendingEmit(
                name="empty_directive_scope_waived", kwargs={"contract": contract}
            )
        )
        ladder.ran("empty_directive_scope")
        ladder.waived.append("empty_directive_scope")
        return None
    missed = empty_directive_missed_tokens(body)
    summary = (
        "Empty directive scope — no actionable scope/todo/packet/"
        "files_expected (empty_directive_scope)."
    )
    if override:
        summary = (
            f"Empty directive scope — {resolved_model_id} is outside the "
            "roaming tier, so a contract override does not waive the scope "
            "bound (empty_directive_scope). Bound the work or bind "
            "composer-2.5/sonnet-5."
        )
    ladder.emits.append(
        PendingEmit(
            name="empty_directive_scope_blocked",
            kwargs={
                "contract": contract,
                "density": density,
                "missed_tokens": missed,
            },
        )
    )
    return ladder.refuse(
        GateRefusal(
            gate="empty_directive_scope",
            reason="empty_directive_scope",
            summary=summary,
            payload={
                "summary": summary,
                "reason": "empty_directive_scope",
                "contract": contract,
                "density": density,
                "missed_tokens": list(missed),
                "resolved_model": resolved_model_id,
                "scope_waiver_withheld": override,
                "fix_hint": EMPTY_SCOPE_FIX_HINT,
            },
        )
    )


def _vision_gates(
    ladder: _Ladder, *, body: str, contract: str, directive: Any
) -> BodyPureVerdict | None:
    """``vision_field_missing`` then ``options_symmetry``."""
    if contract not in VISION_REQUIRED_CONTRACTS or directive is None:
        return None
    if not has_vision_field(body):
        summary = (
            "Directive vision field missing — implement/investigate DIRECTIVEs "
            "require a vision: line (vision_field_missing)."
        )
        return ladder.refuse(
            GateRefusal(
                gate="vision_field_missing",
                reason="vision_field_missing",
                summary=summary,
                payload={
                    "summary": summary,
                    # reason = observed gate identity (which gate fired).
                    "reason": "vision_field_missing",
                    "contract": contract,
                    "density": directive.density,
                    # fix_hint = derived counsel (row 29 member 4 proof slice).
                    "fix_hint": claimed_derived(
                        VISION_MISSING_FIX_HINT,
                        basis="admit_gates.vision_field_missing",
                    ).to_wire(),
                },
            )
        )
    ladder.ran("vision_field_missing")
    admission = admit_options_body(body)
    if not admission.approved and admission.error is not None:
        summary = admission.error["summary"]
        return ladder.refuse(
            GateRefusal(
                gate="options_symmetry",
                reason=str(admission.error["reason"]),
                summary=summary,
                payload={
                    **admission.error,
                    "contract": contract,
                    "density": directive.density,
                    "fix_hint": claimed_derived(
                        admission.error.get("fix_hint", OPTIONS_SYMMETRY_FIX_HINT),
                        basis=f"admit_gates.{admission.error['reason']}",
                    ).to_wire(),
                },
            )
        )
    ladder.ran("options_symmetry")
    return None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def admission_not_applicable(*, scope: str, reason: str) -> dict[str, Any]:
    """Projection for a return path where no job entered the ladder.

    Positively states that nothing was admitted — ``no-auto-handler``,
    ``static-pin-refused``, a failed enqueue, or a continuity hop, which routes
    around admit entirely.
    """
    return {
        "outcome": OUTCOME_NOT_APPLICABLE,
        "reason": reason,
        "as_of": _now_iso(),
        "source": ADMISSION_AUTHORITY,
        "scope": scope,
        "coverage": {
            "asserted": [],
            "deferred": [],
            "not_applicable": list(BODY_PURE_GATES + THREAD_STATE_GATES),
            "waived": [],
        },
        "recovery": ADMISSION_RECOVERY,
    }


def admission_from_verdict(verdict: BodyPureVerdict, *, scope: str) -> dict[str, Any]:
    """Project a ladder verdict onto the wire, qualified by its own coverage."""
    projection: dict[str, Any] = {
        "outcome": verdict.outcome,
        "reason": verdict.refusal.reason if verdict.refusal is not None else None,
        "as_of": _now_iso(),
        "source": ADMISSION_AUTHORITY,
        "scope": scope,
        "coverage": {
            "asserted": list(verdict.asserted),
            "deferred": list(verdict.deferred),
            "not_applicable": list(verdict.not_applicable),
            "waived": list(verdict.waived),
        },
        "recovery": ADMISSION_RECOVERY,
    }
    if verdict.refusal is not None:
        payload = verdict.refusal.payload
        if payload.get("fix_hint") is not None:
            projection["fix_hint"] = payload["fix_hint"]
        if payload.get("missed_tokens") is not None:
            projection["missed_tokens"] = list(payload["missed_tokens"])
    return projection


__all__ = [
    "ADMISSION_AUTHORITY",
    "ADMISSION_RECOVERY",
    "BODY_PURE_GATES",
    "BodyPureVerdict",
    "GateRefusal",
    "OUTCOME_ADMITTED",
    "OUTCOME_DEFERRED",
    "OUTCOME_NOT_APPLICABLE",
    "OUTCOME_REFUSED",
    "OUTCOME_WAIVED",
    "PendingEmit",
    "THREAD_STATE_GATES",
    "admission_from_verdict",
    "admission_not_applicable",
    "admit_gate_entered",
    "evaluate_body_pure_gates",
]
