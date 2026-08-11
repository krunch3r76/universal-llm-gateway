"""Load submit-time ``proof_before`` persisted on open propagation rows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .propagation_ledger import OpenPropagationProjection, get_open_proof_payload


@dataclass(frozen=True)
class PersistedProofBefore:
    """Submit-time before probe extracted from open-row ``proof_payload``."""

    before: dict[str, Any] | None
    stale_code_ref: bool = False
    malformed: bool = False


def load_persisted_proof_before(row: OpenPropagationProjection) -> PersistedProofBefore:
    """Return persisted before when present, valid, and bound to the row's code_ref."""
    raw_payload = get_open_proof_payload(row.row_id)
    if raw_payload is None:
        return PersistedProofBefore(before=None)
    stored_ref = raw_payload.get("code_ref_at_submit")
    if isinstance(stored_ref, str) and stored_ref != row.code_ref:
        return PersistedProofBefore(before=None, stale_code_ref=True)
    candidate = raw_payload.get("proof_before")
    if candidate is None:
        return PersistedProofBefore(before=None)
    if not isinstance(candidate, dict):
        return PersistedProofBefore(before=None, malformed=True)
    return PersistedProofBefore(before=candidate)


def identity_surface_for_row(row: OpenPropagationProjection) -> str:
    """Match ``proof_observed`` / retire surface selection for one row."""
    if row.proof_class == "client_visible" and row.service == "mcp":
        return "mcp_health"
    if row.proof_class == "served_artifact":
        return "liveness"
    return "default"


def defer_reason_and_detail_for_identity(
    row: OpenPropagationProjection,
    *,
    payload: dict[str, Any],
    observed_code_version: str | None,
    persisted: PersistedProofBefore,
) -> tuple[str, str] | None:
    """Return defer reason + detail when equal-ref settle cannot close yet."""
    if persisted.stale_code_ref:
        return (
            "proof_stale_before_code_ref",
            (
                f"persisted proof_before was for a different code_ref "
                f"than {row.code_ref!r} — row left open"
            ),
        )
    if persisted.malformed:
        return (
            "proof_malformed_before",
            "persisted proof_before was not a dict — row left open",
        )
    if observed_code_version and persisted.before is None:
        return (
            "proof_pending",
            (
                "code_version readable but persisted proof_before absent — "
                "identity attestation indeterminate — row left open"
            ),
        )
    if persisted.before is not None:
        from services.git_integration_worker.cursor_auto.propagation_probe import (
            proof_identity_attestation,
        )

        attestation = proof_identity_attestation(
            persisted.before,
            payload,
            service=row.service,
            surface=identity_surface_for_row(row),
        )
        if attestation == "unchanged":
            return (
                "proof_identity_unchanged",
                "identity attestation unchanged — row left open",
            )
        if attestation == "indeterminate":
            return (
                "proof_identity_indeterminate",
                "identity attestation indeterminate — row left open",
            )
        if attestation == "fall_through":
            return (
                "proof_identity_fall_through",
                "identity attestation fall_through — row left open",
            )
    return None


def proof_before_payload_for_submit(
    *,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    code_ref: str,
    manage_status: str | None,
    proof_class_requested: str | None,
    proof_class_executed: str | None,
) -> dict[str, Any]:
    """Structured open-row payload persisted at async submit without closing."""
    payload: dict[str, Any] = {
        "proof_before": before,
        "proof_after_immediate": after,
        "code_ref_at_submit": code_ref,
    }
    if manage_status is not None:
        payload["manage_status"] = manage_status
    if proof_class_requested is not None:
        payload["proof_class_requested"] = proof_class_requested
    if proof_class_executed is not None:
        payload["proof_class_executed"] = proof_class_executed
    return payload


__all__ = [
    "PersistedProofBefore",
    "defer_reason_and_detail_for_identity",
    "identity_surface_for_row",
    "load_persisted_proof_before",
    "proof_before_payload_for_submit",
]
