"""Settle terminalization for unresolvable / deploy-line-unreachable rows.

STATUS_CLAIM_KIND=observed_of_attempt: these fails freeze one obligation
*attempt*. They are not satisfied-closes and not standing fleet debt.
"""

from __future__ import annotations

from typing import Any

from .propagation_code_ref_mint import (
    REASON_UNSATISFIABLE_CODE_REF,
    REASON_UNSATISFIABLE_DEPLOY_LINE,
    try_recover_code_ref,
)
from .propagation_ledger import (
    OpenPropagationProjection,
    fail_row,
    set_open_proof_payload,
)
from .propagation_version_satisfaction import (
    DEFER_UNRELATED,
    VersionSatisfaction,
)

# Imported late-shape avoid cycle: SettleResult lives on propagation_terminal.
_INDETERMINATE_PROBE = "proof_indeterminate_probe_unreadable"


def try_terminalize_unsatisfiable(
    row: OpenPropagationProjection,
    *,
    payload: dict[str, Any],
    observed: str | None,
    satisfaction: VersionSatisfaction,
    determination: str,
    defer_if_unreachable: bool,
    settle_result_cls: type,
) -> Any | None:
    """Fail or defer unresolvable/unrelated cases; None if not applicable."""
    relation = satisfaction.relation
    if satisfaction.case == "unresolvable":
        recovered: str | None = None
        if str(row.code_ref or "").strip().upper() != "HEAD":
            recovered = try_recover_code_ref(row.code_ref)
            if recovered is None:
                for field in (row.reason, row.hazard, row.proof):
                    if field:
                        recovered = try_recover_code_ref(str(field))
                        if recovered is not None:
                            break
        fail_payload = {
            **payload,
            "expected_code_ref": row.code_ref,
            "observed_code_version": observed,
            "code_ref_relation": relation,
            "version_satisfaction_case": satisfaction.case,
        }
        if recovered is not None:
            fail_payload["recovered_code_ref"] = recovered
        fail_row(
            row.row_id,
            proof_payload=fail_payload,
            reason=REASON_UNSATISFIABLE_CODE_REF,
        )
        return settle_result_cls(
            row_id=row.row_id,
            service=row.service,
            code_ref=row.code_ref,
            outcome="failed",
            detail=(
                f"unsatisfiable code_ref {row.code_ref!r} "
                f"(observed {observed!r}"
                + (
                    f"; recovered_code_ref={recovered}"
                    if recovered is not None
                    else ""
                )
                + ")"
            ),
        )

    if satisfaction.case != "unrelated":
        return None

    if determination == "indeterminate" or relation == "unknown":
        reason = (
            _INDETERMINATE_PROBE
            if determination == "indeterminate"
            else DEFER_UNRELATED
        )
        detail = (
            "probe carried no readable code_version — row left open"
            if determination == "indeterminate"
            else (
                f"unrelated pending readable observed "
                f"(expected {row.code_ref}) — row left open"
            )
        )
        observation = {
            **payload,
            "expected_code_ref": row.code_ref,
            "observed_code_version": observed,
            "code_ref_relation": relation,
            "version_satisfaction_case": satisfaction.case,
        }
        if defer_if_unreachable:
            set_open_proof_payload(
                row.row_id,
                proof_payload=observation,
                defer_reason=reason,
            )
        else:
            set_open_proof_payload(row.row_id, proof_payload=observation)
        return settle_result_cls(
            row_id=row.row_id,
            service=row.service,
            code_ref=row.code_ref,
            outcome="deferred" if defer_if_unreachable else "unsettled",
            detail=detail,
        )

    fail_payload = {
        **payload,
        "expected_code_ref": row.code_ref,
        "observed_code_version": observed,
        "code_ref_relation": relation,
        "version_satisfaction_case": satisfaction.case,
        "deploy_line_predicate": "not_ancestor_of_observed_live",
    }
    fail_row(
        row.row_id,
        proof_payload=fail_payload,
        reason=REASON_UNSATISFIABLE_DEPLOY_LINE,
    )
    return settle_result_cls(
        row_id=row.row_id,
        service=row.service,
        code_ref=row.code_ref,
        outcome="failed",
        detail=(
            f"deploy-line unreachable: expected {row.code_ref} "
            f"observed {observed!r}; relation={relation} — "
            f"{satisfaction.reader_entitlement}"
        ),
    )


__all__ = ["try_terminalize_unsatisfiable"]
