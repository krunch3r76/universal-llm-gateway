"""Reconcile open propagation rows whose proof_class cannot be satisfied."""

from __future__ import annotations

from charter_runner_store.propagation_ledger import (
    OpenPropagationProjection,
    fail_row,
)


def reconcile_unsupported_proof_class(row: OpenPropagationProjection) -> str | None:
    """Fail the row when no registered probe exists for its proof_class.

    Returns the fail-loud error token when the row was terminated, else ``None``.
    """
    from scripts.model_manager.ui.controller.charter_runner.propagation_execute import (
        dispatch_for_projection,
    )

    result = dispatch_for_projection(row)
    if result.error is None:
        return None
    fail_row(
        row.row_id,
        proof_payload={
            "proof_class_requested": result.proof_class_requested,
            "proof_class_executed": result.proof_class_executed,
        },
        reason=result.error,
    )
    return result.error


__all__ = ["reconcile_unsupported_proof_class"]
