"""Non-writing preflight collector for implement-ready admission gates."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from implement_admission.dense_spec_schema import (
    dense_spec_hash_uri,
    validate_dense_spec,
)
from implement_admission.density_triage_gate import (
    JUDGMENT_REQUIRED,
    MECHANICAL,
    RECON_PENDING,
    format_implement_triage_unknown_reason,
)
from implement_admission.implement_ready import (
    _acceptance_unpopulated_or_default,
    _assertion_cites_dense_spec,
    _assertion_inactive,
    _skeptic_evidence_reject,
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


class GateStatus(StrEnum):
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
    recon_waiver: dict[str, Any] | None = None

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
            **({"recon_waiver": self.recon_waiver} if self.recon_waiver else {}),
        }


@dataclass
class PreflightReport:
    """Preflight admission over declared-state gates 0-13.

    Gate-13 skeptic evidence-grounding: when the caller supplies the
    grounding outcome (``skeptic_evidence_grounded`` is not None) the same
    sub-checks the implement dispatch enforces are evaluated here and a
    failing outcome FAILS gate 13 with the dispatch's code (for example
    ``skeptic_evidence_missing``). When grounding could not be evaluated,
    gate 13 passes with ``deferred_subchecks`` AND an explicit entry in
    ``warnings`` naming the FILE_EVIDENCE_PATHS requirement — ``admitted``
    true with empty ``warnings`` implies the implement dispatch will not
    reject on gate-13 grounds for the same inputs.
    """

    admitted: bool
    summary: dict[str, int]
    first_failure: dict[str, str] | None
    resolution: dict[str, Any] | None
    gates: list[GateReport]
    recon_waived: bool = False
    recon_waiver: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.admitted,
            "admitted": self.admitted,
            "summary": self.summary,
            **({"first_failure": self.first_failure} if self.first_failure else {}),
            **({"resolution": self.resolution} if self.resolution else {}),
            "gates": [g.to_dict() for g in self.gates],
            "recon_waived": self.recon_waived,
            **({"recon_waiver": self.recon_waiver} if self.recon_waiver else {}),
            "warnings": list(self.warnings),
        }


def _make_report(
    *,
    admitted: bool,
    gates: list[GateReport],
    first_failure: dict[str, str] | None,
    resolution: dict[str, Any] | None,
    recon_waived: bool = False,
    recon_waiver: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> PreflightReport:
    failed = sum(1 for g in gates if g.status == GateStatus.FAILED)
    blocked_count = sum(1 for g in gates if g.status == GateStatus.BLOCKED)
    na_count = sum(1 for g in gates if g.status == GateStatus.NOT_APPLICABLE)
    return PreflightReport(
        admitted=admitted,
        summary={
            "failed": failed,
            "blocked": blocked_count,
            "not_applicable": na_count,
        },
        first_failure=first_failure,
        resolution=resolution,
        gates=gates,
        recon_waived=recon_waived,
        recon_waiver=recon_waiver,
        warnings=warnings or [],
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
    recon_waiver: dict[str, Any] | None = None,
    authoring_mode: bool = False,
    skeptic_evidence_grounded: bool | None = None,
    skeptic_evidence_unresolved: list[str] | None = None,
    skeptic_evidence_mode: str | None = None,
    skeptic_unratified_reason: str | None = None,
) -> PreflightReport:
    """Non-writing preflight over the declared-state gates (0-13).

    ``authoring_mode`` marks a bare-spec validation with no real todo behind
    it (doc_validate called with ``text=``/``path=`` instead of
    ``source_ref=``). Todo-linkage gates (2-5, 7, 10-13) report
    ``not_applicable`` in this mode rather than evaluating fabricated
    stand-in values — those gates test facts about a todo entity that does
    not exist here. Spec-content gates (6, 8, 9) still evaluate for real.

    Gate 13 (skeptic_pass): when the caller supplies the skeptic
    evidence-grounding outcome (``skeptic_evidence_grounded`` is not None),
    the same sub-checks the implement dispatch enforces
    (``_skeptic_evidence_reject``) are evaluated here and a failing outcome
    FAILS gate 13 with the dispatch code (``skeptic_evidence_missing`` /
    ``skeptic_evidence_unresolved`` / ...). When grounding is not supplied,
    gate 13 passes with ``deferred_subchecks`` and an explicit warning
    naming the FILE_EVIDENCE_PATHS requirement so an admitted report is
    never silently weaker than the dispatch gate.
    """
    gates: list[GateReport] = []
    blocked: set[int] = set()
    first_failure: dict[str, str] | None = None
    warnings: list[str] = []

    def _pass(
        gate_id: int,
        name: str,
        *,
        deferred_subchecks: tuple[str, ...] = (),
        gate_recon_waiver: dict[str, Any] | None = None,
    ) -> None:
        gates.append(
            GateReport(
                gate=gate_id,
                name=name,
                status=GateStatus.PASSED,
                deferred_subchecks=deferred_subchecks,
                recon_waiver=gate_recon_waiver,
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

    if triage == MECHANICAL:
        _pass(0, "mechanical_bypass")
        for i in range(1, 14):
            _na(i, _GATE_NAMES[i])
        return PreflightReport(
            admitted=True,
            summary={"failed": 0, "blocked": 0, "not_applicable": 13},
            first_failure=None,
            resolution=resolution,
            gates=gates,
        )
    _na(0, "mechanical_bypass")

    if triage == RECON_PENDING:
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

    if triage != JUDGMENT_REQUIRED:
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

    if authoring_mode:
        _na(2, "assertion_pin_present")
    elif implement_ready_assertion_id is None:
        code = "implement_not_ready_judgment_required"
        reason = (
            f"{todo_id}: judgment_required but implement_ready_assertion_id is "
            "absent — record an implement-ready assertion citing the dense spec"
        )
        _fail(2, "assertion_pin_present", code, reason)
        blocked.update({3, 4, 5})
    else:
        _pass(2, "assertion_pin_present")

    if authoring_mode:
        _na(3, "assertion_row_exists")
    elif 3 in blocked:
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

    if authoring_mode:
        _na(4, "assertion_entity_bind")
    elif 4 in blocked:
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

    if authoring_mode:
        _na(5, "assertion_active")
    elif 5 in blocked:
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

    if authoring_mode:
        _na(7, "spec_cited_in_evidence")
    elif 7 in blocked:
        parents = tuple(
            p for p in (3, 6) if p in blocked or gates[p].status != GateStatus.PASSED
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

    if authoring_mode:
        _na(10, "content_hash_attested")
    elif 10 in blocked:
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

    if authoring_mode:
        _na(11, "files_expected_distilled")
    elif 11 in blocked:
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

    if authoring_mode:
        _na(12, "acceptance_criteria_distilled")
    elif 12 in blocked:
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

    if authoring_mode:
        _na(13, "skeptic_pass")
    elif not skeptic_ratified and not recon_waived:
        code = "skeptic_pass_missing"
        reason = (
            f"{todo_id}: judgment_required (material) decision needs a skeptic "
            f"ratification before implement — record a confirmed "
            f"status({todo_id}, skeptic_ratified, current) assertion citing the "
            "skeptic/panel thread AND the spec_sha256:<hex> URI of the current "
            "dense-spec content in evidence_uris (run the axis-2 skeptic pass "
            "per cheap-recon-before-escalation)."
        )
        if skeptic_unratified_reason:
            reason += f" Unmet subcondition: {skeptic_unratified_reason}"
        _fail(13, "skeptic_pass", code, reason)
    elif skeptic_ratified and not recon_waived:
        if skeptic_evidence_grounded is None:
            _pass(13, "skeptic_pass", deferred_subchecks=_GATE_13_DEFERRED_SUBCHECKS)
            warnings.append(
                "gate 13 skeptic evidence-grounding was not evaluated at "
                "preflight: the implement dispatch additionally requires the "
                "ratifying skeptic bus turn to contain a FILE_EVIDENCE_PATHS "
                "block with resolvable file paths (inline-only skeptics echo "
                "dispatcher-supplied paths). If the cited turn lacks it, the "
                "dispatch will reject with skeptic_evidence_missing (or "
                "skeptic_evidence_unresolved / skeptic_evidence_malformed / "
                "skeptic_evidence_stamp_missing)."
            )
        else:
            evidence_reject = _skeptic_evidence_reject(
                todo_id=todo_id,
                evidence_grounded=skeptic_evidence_grounded,
                evidence_unresolved=skeptic_evidence_unresolved,
                evidence_mode=skeptic_evidence_mode,
            )
            if evidence_reject is not None:
                _fail(
                    13,
                    "skeptic_pass",
                    evidence_reject.code or "skeptic_evidence_missing",
                    evidence_reject.reason or "skeptic evidence not grounded",
                )
            else:
                _pass(13, "skeptic_pass")
    else:
        gate_waiver = recon_waiver if recon_waived and recon_waiver else None
        _pass(13, "skeptic_pass", gate_recon_waiver=gate_waiver)

    return _make_report(
        admitted=first_failure
        is None,  # gates 0-13 declared-state only; ¬ full evaluator
        gates=gates,
        first_failure=first_failure,
        resolution=resolution,
        recon_waived=recon_waived,
        recon_waiver=recon_waiver if recon_waived else None,
        warnings=warnings,
    )


__all__ = [
    "GateReport",
    "GateStatus",
    "PreflightReport",
    "preflight_implement_ready",
]
