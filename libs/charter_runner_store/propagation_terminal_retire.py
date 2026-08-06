"""Close open propagation rows when observed liveness already satisfies code_ref.

D2 retirement: ancestor obligations close when a descendant is live; equal-ref
closes when process identity or the owed service surface is attested. Harvest
fire still demands a before/after identity delta for equal-ref process_live.
"""

from __future__ import annotations

from typing import Any

from deploy_identity.code_ref_relation import code_ref_satisfied

from .propagation_ledger import OpenPropagationProjection, close_row
from .propagation_version_satisfaction import classify_version_satisfaction


def observed_code_version_for_settle(
    row: OpenPropagationProjection,
    payload: dict[str, Any],
) -> str | None:
    """Prefer flat code_version; for mcp client_visible read mcp_health.

    Dual-surface client_visible proofs still require cortex for harvest fire.
    Settle retirement classifies version satisfaction from the owed service's
    own live surface so a lagging peer does not freeze already-live debt.
    """
    observed = payload.get("code_version")
    if isinstance(observed, str) and observed.strip():
        return observed
    if row.service == "mcp" and row.proof_class == "client_visible":
        section = payload.get("mcp_health")
        if isinstance(section, dict):
            nested = section.get("code_version")
            if isinstance(nested, str) and nested.strip():
                return nested
    return None


def try_close_on_version_satisfaction(
    row: OpenPropagationProjection,
    payload: dict[str, Any],
    *,
    proof_passes: bool,
    determination: str,
) -> str | None:
    """Close the row when equal/ancestor policy retires it; return detail or None."""
    observed_str = observed_code_version_for_settle(row, payload)
    satisfaction = classify_version_satisfaction(row.code_ref, observed_str)
    relation = satisfaction.relation

    if satisfaction.case == "exact_match":
        from services.git_integration_worker.cursor_auto.propagation_probe import (
            strong_process_identity,
        )

        identity_attested = (
            row.proof_class == "process_live" and strong_process_identity(payload)
        )
        mcp_surface_attested = (
            row.service == "mcp"
            and row.proof_class == "client_visible"
            and observed_str is not None
            and code_ref_satisfied(row.code_ref, observed_str)
        )
        if (
            (determination == "matched" and proof_passes)
            or identity_attested
            or mcp_surface_attested
        ):
            close_payload = {
                **payload,
                "code_ref_relation": relation,
                "version_satisfaction_case": satisfaction.case,
                "identity_attested": identity_attested,
                "mcp_surface_attested": mcp_surface_attested,
            }
            close_row(row.row_id, proof_payload=close_payload)
            detail = f"exact match: code_version equals code_ref={row.code_ref}"
            if identity_attested and not proof_passes:
                detail += " (live process identity attested)"
            if mcp_surface_attested and not proof_passes:
                detail += " (mcp surface attested; cortex peer not required for retire)"
            return detail

    if satisfaction.case == "ancestry_satisfied":
        close_payload = {
            **payload,
            "code_ref_relation": relation,
            "version_satisfaction_case": satisfaction.case,
        }
        close_row(row.row_id, proof_payload=close_payload)
        return (
            f"ancestry satisfied: newer code live "
            f"(expected {row.code_ref} observed {observed_str!r}; "
            f"relation={relation}) — retired"
        )

    return None


__all__ = [
    "observed_code_version_for_settle",
    "try_close_on_version_satisfaction",
]
