"""Git integration library — atomic arc-worktree merge mechanics."""

from __future__ import annotations

from git_integrate.events import (
    GitIntegrateCompleted,
    GitIntegrateGateFailed,
    GitIntegrateRejected,
    GitIntegrateRequested,
    GitIntegrateRetried,
    GitStatusRead,
    emit_git_integrate_completed,
    emit_git_integrate_gate_failed,
    emit_git_integrate_rejected,
    emit_git_integrate_requested,
    emit_git_integrate_retried,
    emit_git_status_read,
    register_uds_publisher,
)
from git_integrate.integrate import integrate_op
from git_integrate.schema import CasResult, IntegrateResult, MergeResult

__all__ = [
    "CasResult",
    "GitIntegrateCompleted",
    "GitIntegrateGateFailed",
    "GitIntegrateRejected",
    "GitIntegrateRequested",
    "GitIntegrateRetried",
    "GitStatusRead",
    "IntegrateResult",
    "MergeResult",
    "emit_git_integrate_completed",
    "emit_git_integrate_gate_failed",
    "emit_git_integrate_rejected",
    "emit_git_integrate_requested",
    "emit_git_integrate_retried",
    "emit_git_status_read",
    "integrate_op",
    "register_uds_publisher",
]
