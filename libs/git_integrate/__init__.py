"""Git integration library — atomic arc-worktree merge mechanics."""

from __future__ import annotations

from git_integrate.commit import commit_op
from git_integrate.events import (
    GitIntegrateCompleted,
    GitIntegrateGateFailed,
    GitIntegrateRejected,
    GitIntegrateRequested,
    GitIntegrateRetried,
    GitLogRead,
    GitStatusRead,
    emit_git_integrate_completed,
    emit_git_integrate_gate_failed,
    emit_git_integrate_rejected,
    emit_git_integrate_requested,
    emit_git_integrate_retried,
    emit_git_log_read,
    emit_git_status_read,
    register_uds_publisher,
)
from git_integrate.integrate import integrate_op
from git_integrate.land import land_op
from git_integrate.schema import CasResult, CommitResult, IntegrateResult, MergeResult

__all__ = [
    "CasResult",
    "CommitResult",
    "GitIntegrateCompleted",
    "GitIntegrateGateFailed",
    "GitIntegrateRejected",
    "GitIntegrateRequested",
    "GitIntegrateRetried",
    "GitLogRead",
    "GitStatusRead",
    "IntegrateResult",
    "MergeResult",
    "emit_git_integrate_completed",
    "emit_git_integrate_gate_failed",
    "emit_git_integrate_rejected",
    "emit_git_integrate_requested",
    "emit_git_integrate_retried",
    "emit_git_log_read",
    "emit_git_status_read",
    "commit_op",
    "integrate_op",
    "land_op",
    "register_uds_publisher",
]
