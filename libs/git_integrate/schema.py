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
RC_DIRTY_WORKTREE = "dirty_worktree"
RC_DIRTY_MASTER = "dirty_master"
RC_UNCOMMITTED_NO_MESSAGE = "uncommitted_no_message"
RC_NOTHING_TO_LAND = "nothing_to_land"
RC_COMMIT_FAILED = "commit_failed"
RC_CLEAN_TREE = "clean_tree"
RC_PATHS_EMPTY = "paths_empty"
RC_BRANCH_MISMATCH = "branch_mismatch"
RC_NO_CHANGES_FOR_PATHS = "no_changes_for_paths"

# SHA-256 of empty string — canonical empty diff fingerprint.
EMPTY_DIFF_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


@dataclass(frozen=True, slots=True)
class CommitResult:
    """Outcome of commit_arc."""

    committed: bool
    commit_sha: str = ""
    reason_code: str = ""


@dataclass(frozen=True, slots=True)
class IntegrateResult:
    """Admission check result for git arc integration."""

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
