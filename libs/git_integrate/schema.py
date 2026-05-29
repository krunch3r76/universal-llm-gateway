"""Result types and reason-code constants for git integration operations."""

from __future__ import annotations

from dataclasses import dataclass

# Reason-code constants — canonical set for integrate_op outcomes.
RC_ARC_BRANCH_MISMATCH = "arc_branch_mismatch"
RC_APPROVAL_MISSING = "approval_missing"
RC_DIFF_MISMATCH = "diff_mismatch"
RC_INTEGRATE_CONFLICT = "integrate_conflict"
RC_GATE_FAILED = "gate_failed"
RC_CAS_EXHAUSTED = "cas_exhausted"
RC_TEARDOWN_FAILED = "teardown_failed"
RC_NOT_A_GIT_REPO = "not_a_git_repo"
RC_WORKTREE_MISSING = "worktree_missing"


@dataclass(frozen=True, slots=True)
class IntegrateResult:
    """Admission check result — mirrors GitOpResult shape from grokbuild."""

    ok: bool
    reason_code: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class MergeResult:
    """Outcome of merge_master_into."""

    conflict: bool
    merge_commit: str = ""


@dataclass(frozen=True, slots=True)
class CasResult:
    """Outcome of advance_master_cas."""

    non_ff: bool
    new_sha: str = ""
