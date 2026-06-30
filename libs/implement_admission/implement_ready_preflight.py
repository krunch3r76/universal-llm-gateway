"""Non-writing preflight collector for implement-ready admission gates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from implement_admission.dense_spec_schema import dense_spec_hash_uri, validate_dense_spec
from implement_admission.density_triage_gate import format_implement_triage_unknown_reason
from implement_admission.implement_ready import (
    _acceptance_unpopulated_or_default,
    _assertion_cites_dense_spec,
    _assertion_inactive,
)

_GATE_NAMES: dict[int, str] = {
    0: "mechanical_bypass",
    1: "triage_known",
    2: "assertion_pin_present",
    3: "assertion_row_exists",
    4: "assertion_entity_bind",
    5: "assertion_active",
    6: "dense_spec_path",
    7: "spec_cited_in_evidence",
    8: "spec_readable",
    9: "dense_spec_schema",
    10: "content_hash_attested",
    11: "files_expected_distilled",
    12: "acceptance_criteria_distilled",
    13: "skeptic_pass",
}

_GATE_13_DEFERRED_SUBCHECKS: tuple[str, ...] = (
    "skeptic_evidence_grounded",
    "skeptic_evidence_unresolved",
    "skeptic_evidence_mode",
)


class GateStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class GateReport:
    gate: int
    name: str
    status: GateStatus
    code: str | None = None
    reason: str | None = None
    blocked_by: tuple[int, ...] = ()
    deferred_subchecks: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "name": self.name,
            "status": self.status.value,
            **({"code": self.code} if self.code else {}),
            **({"reason": self.reason} if self.reason else {}),
            **({"blocked_by": list(self.blocked_by)} if self.blocked_by else {}),
            **(
                {"deferred_subchecks": list(self.deferred_subchecks)}
                if self.deferred_subchecks
                else {}
            ),
        }


@dataclass
class PreflightReport:
    """Preflight admission over declared-state gates 0-13 only.

    ``admitted`` does NOT imply ``evaluate_implement_ready`` would admit —
    skeptic evidence-grounding is evaluator-only (see gate-13
    ``deferred_subchecks``).
    """

    admitted: bool
    summary: dict[str, int]
    first_failure: dict[str, str] | None
    resolution: dict[str, Any] | None
    gates: list[GateReport]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.admitted,
            "admitted": self.admitted,
            "summary": self.summary,
            **({"first_failure": self.first_failure} if self.first_failure else {}),
            **({"resolution": self.resolution} if self.resolution else {}),
            "gates": [g.to_dict() for g in self.gates],
        }


def _make_report(
    *,
    admitted: bool,
    gates: list[GateReport],
    first_failure: dict[str, str] | None,
    resolution: dict[str, Any] | None,
) -> PreflightReport:
    failed = sum(1 for g in gates if g.status == GateStatus.FAILED)
    blocked_count = sum(1 for g in gates if g.status == GateStatus.BLOCKED)
    na_count = sum(1 for g in gates if g.status == GateStatus.NOT_APPLICABLE)
    return PreflightReport(
        admitted=admitted,
        summary={"failed": failed, "blocked": blocked_count, "not_applicable": na_count},
        first_failure=first_failure,
        resolution=resolution,
        gates=gates,
    )


def preflight_implement_ready(
    *,
    todo_id: str,
    density_triage: str | None,
    source_uri: str | None,
    implement_ready_assertion_id: int | None,
    assertion: dict | None,
    now_iso: str,
    dense_spec_uri: str | None = None,
    dense_spec_text: str | None = None,
    files_expected: list[str] | None = None,
    acceptance_criteria: list[str] | None = None,
    entity_name: str | None = None,
    resolution: dict[str, Any] | None = None,
    skeptic_ratified: bool = False,
    recon_waived: bool = False,
) -> PreflightReport:
    """Non-writing preflight over the declared-state gates (0-13).

    NOTE: gate 13 (skeptic_pass) reports only on skeptic ratification /
    recon waiver. The skeptic-evidence-grounding sub-checks enforced by
    evaluate_implement_ready (_skeptic_evidence_reject) are intentionally NOT
    evaluated here -- preflight lacks those inputs -- so a PASSED gate 13 does
    not guarantee the full gate admits. See
    test_gate14_intentionally_stargate_only_preflight_admits.

    ``admitted`` is true over preflight-available declared-state gates 0-13
    only; it does NOT imply ``evaluate_implement_ready`` would admit.
    """
    gates: list[GateReport] = []
    blocked: set[int] = set()
    first_failure: dict[str, str] | None = None

    def _pass(
        gate_id: int,
        name: str,
        *,
        deferred_subchecks: tuple[str, ...] = (),
    ) -> None:
        gates.append(
            GateReport(
                gate=gate_id,
                name=name,
                status=GateStatus.PASSED,
                deferred_subchecks=deferred_subchecks,
            )
        )

    def _fail(
        gate_id: int,
        name: str,
        code: str,
        reason: str,
        also_block: tuple[int, ...] = (),
    ) -> None:
        nonlocal first_failure
        gates.append(
            GateReport(
                gate=gate_id,
                name=name,
                status=GateStatus.FAILED,
                code=code,
                reason=reason,
            )
        )
        blocked.update(also_block)
        if first_failure is None:
            first_failure = {"code": code, "reason": reason}

    def _block(gate_id: int, name: str, blocked_by: tuple[int, ...]) -> None:
        gates.append(
            GateReport(
                gate=gate_id,
                name=name,
                status=GateStatus.BLOCKED,
                blocked_by=blocked_by,
            )
        )

    def _na(gate_id: int, name: str) -> None:
        gates.append(
            GateReport(gate=gate_id, name=name, status=GateStatus.NOT_APPLICABLE)
        )

    triage = (density_triage or "").strip() or None

    if triage == "mechanical":
        _na(0, "mechanical_bypass")
        for i in range(1, 14):
            _na(i, _GATE_NAMES[i])
        return PreflightReport(
            admitted=True,
            summary={"failed": 0, "blocked": 0, "not_applicable": 14},
            first_failure=None,
            resolution=resolution,
            gates=gates,
        )
    _pass(0, "mechanical_bypass")

    if triage == "recon_pending":
        code = "implement_blocked_recon_pending"
        reason = (
            f"{todo_id}: recon not complete — run the two-axis recon and "
            "re-triage to judgment_required or mechanical before implement dispatch"
        )
        _fail(1, "triage_known", code, reason, also_block=tuple(range(2, 14)))
        for i in range(2, 14):
            _block(i, _GATE_NAMES[i], blocked_by=(1,))
        return _make_report(
            admitted=False,
            gates=gates,
            first_failure=first_failure,
            resolution=resolution,
        )

    if triage != "judgment_required":
        code = "implement_triage_unknown"
        reason = format_implement_triage_unknown_reason(todo_id, density_triage)
        _fail(1, "triage_known", code, reason, also_block=tuple(range(2, 14)))
        for i in range(2, 14):
            _block(i, _GATE_NAMES[i], blocked_by=(1,))
        return _make_report(
            admitted=False,
            gates=gates,
            first_failure=first_failure,
            resolution=resolution,
        )
    _pass(1, "triage_known")

    if implement_ready_assertion_id is None:
        code = "implement_not_ready_judgment_required"
        reason = (
            f"{todo_id}: judgment_required but implement_ready_assertion_id is "
            "absent — record an implement-ready assertion citing the dense spec"
        )
        _fail(2, "assertion_pin_present", code, reason)
        blocked.update({3, 4, 5})
    else:
        _pass(2, "assertion_pin_present")

    if 3 in blocked:
        _block(3, "assertion_row_exists", blocked_by=(2,))
        blocked.update({4, 5, 7})
    elif assertion is None:
        code = "implement_ready_assertion_missing"
        reason = (
            f"{todo_id}: implement_ready_assertion_id={implement_ready_assertion_id} "
            "does not resolve to an assertion row"
        )
        _fail(3, "assertion_row_exists", code, reason)
        blocked.update({4, 5, 7})
    else:
        _pass(3, "assertion_row_exists")

    if 4 in blocked:
        _block(4, "assertion_entity_bind", blocked_by=(2, 3))
    elif assertion.get("entity_id") != todo_id:
        code = "implement_ready_assertion_entity_mismatch"
        reason = (
            f"{todo_id}: assertion {implement_ready_assertion_id} is bound to "
            f"{assertion.get('entity_id')!r}, not this todo"
        )
        _fail(4, "assertion_entity_bind", code, reason)
    else:
        _pass(4, "assertion_entity_bind")

    if 5 in blocked:
        _block(5, "assertion_active", blocked_by=(2, 3))
    elif _assertion_inactive(assertion, now_iso=now_iso):
        code = "implement_ready_assertion_inactive"
        reason = (
            f"{todo_id}: assertion {implement_ready_assertion_id} is superseded "
            "or expired — record a fresh implement-ready declaration"
        )
        _fail(5, "assertion_active", code, reason)
    else:
        _pass(5, "assertion_active")

    dense_uri = (source_uri or "").strip()
    if not dense_uri:
        code = "implement_not_ready_no_dense_spec"
        reason = (
            f"{todo_id}: source_uri must point at tasks/specs/{{slug}}.md or "
            "notes/system/specs/{slug}.md before implement dispatch"
        )
        _fail(6, "dense_spec_path", code, reason)
        blocked.update({7, 8, 9, 10})
    else:
        _pass(6, "dense_spec_path")

    if 7 in blocked:
        parents = tuple(
            p
            for p in (3, 6)
            if p in blocked or gates[p].status != GateStatus.PASSED
        )
        _block(7, "spec_cited_in_evidence", blocked_by=parents or (6,))
    else:
        evidence = assertion.get("evidence_uris") if assertion else None
        if not isinstance(evidence, list):
            evidence = None
        if not _assertion_cites_dense_spec(evidence, source_uri=dense_uri):
            code = "implement_ready_assertion_spec_uncited"
            reason = (
                f"{todo_id}: assertion {implement_ready_assertion_id} must cite "
                f"the dense spec ({dense_uri}) in evidence_uris"
            )
            _fail(7, "spec_cited_in_evidence", code, reason)
        else:
            _pass(7, "spec_cited_in_evidence")

    if 8 in blocked:
        _block(8, "spec_readable", blocked_by=(6,))
    elif dense_spec_text is None:
        code = "implement_spec_unreadable"
        reason = (
            f"{todo_id}: dense spec at {dense_spec_uri or dense_uri} could not "
            "be read for schema validation"
        )
        _fail(8, "spec_readable", code, reason)
        blocked.update({9, 10})
    else:
        _pass(8, "spec_readable")

    if 9 in blocked:
        _block(9, "dense_spec_schema", blocked_by=(8,))
        blocked.add(10)
    else:
        schema = validate_dense_spec(dense_spec_text)
        if not schema.passed:
            code = "implement_spec_not_dense"
            reason = (
                f"{todo_id}: {dense_spec_uri or dense_uri} fails dense-spec schema "
                f"({schema.code}: {schema.reason})"
            )
            _fail(9, "dense_spec_schema", code, reason)
            blocked.add(10)
        else:
            _pass(9, "dense_spec_schema")

    if 10 in blocked:
        parents = tuple(
            p
            for p in (8, 9)
            if p in blocked
            or gates[p].status not in (GateStatus.PASSED, GateStatus.NOT_APPLICABLE)
        )
        _block(10, "content_hash_attested", blocked_by=parents or (9,))
    else:
        evidence_check = assertion.get("evidence_uris") if assertion else None
        if not isinstance(evidence_check, list):
            evidence_check = []
        if dense_spec_hash_uri(dense_spec_text) not in evidence_check:
            code = "implement_spec_drifted_since_ready"
            reason = (
                f"{todo_id}: current spec content is not attested by assertion "
                f"{implement_ready_assertion_id} (cite spec_sha256:<hex> of the "
                "validated content; rerun the validator and refresh the assertion)"
            )
            _fail(10, "content_hash_attested", code, reason)
        else:
            _pass(10, "content_hash_attested")

    if 11 in blocked:
        _block(11, "files_expected_distilled", blocked_by=(1,))
    elif not files_expected:
        code = "implement_attrs_unpopulated"
        reason = (
            f"{todo_id}: implement-ready but attrs.files_expected is empty — "
            "distill files_expected from the dense spec at Gate-2 close."
        )
        _fail(11, "files_expected_distilled", code, reason)
    else:
        _pass(11, "files_expected_distilled")

    if 12 in blocked:
        _block(12, "acceptance_criteria_distilled", blocked_by=(1,))
    elif _acceptance_unpopulated_or_default(
        acceptance_criteria,
        todo_id=todo_id,
        name=entity_name,
    ):
        code = "implement_attrs_unpopulated"
        reason = (
            f"{todo_id}: attrs.acceptance_criteria is empty or the default "
            "placeholder — distill acceptance_criteria from the dense spec at "
            "Gate-2 close."
        )
        _fail(12, "acceptance_criteria_distilled", code, reason)
    else:
        _pass(12, "acceptance_criteria_distilled")

    if not skeptic_ratified and not recon_waived:
        code = "skeptic_pass_missing"
        reason = (
            f"{todo_id}: judgment_required (material) decision needs a skeptic "
            f"ratification before implement — record a confirmed "
            f"status({todo_id}, skeptic_ratified, current) assertion citing the "
            "skeptic/panel thread (run the axis-2 skeptic pass per "
            "cheap-recon-before-escalation)."
        )
        _fail(13, "skeptic_pass", code, reason)
    else:
        _pass(13, "skeptic_pass", deferred_subchecks=_GATE_13_DEFERRED_SUBCHECKS)

    return _make_report(
        admitted=first_failure is None,  # gates 0-13 declared-state only; ¬ full evaluator
        gates=gates,
        first_failure=first_failure,
        resolution=resolution,
    )


__all__ = [
    "GateReport",
    "GateStatus",
    "PreflightReport",
    "preflight_implement_ready",
]
