"""Request/response models for ``/api/v1/git/*`` (descriptor source of truth)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class IntegrateRequest(BaseModel):
    """Body for ``POST /integrate`` — ``green_gate_cmd`` is server-owned only."""

    model_config = ConfigDict(extra="forbid")

    arc: str = Field(..., description="Plan slug; worktree branch must be arc/<arc>.")
    phase: str = Field(..., description="Phase label for audit events.")
    worktree_path: str = Field(..., description="Absolute path to the arc worktree.")
    approval: str = Field(
        ...,
        description="Operator approval bound to expected_diff_sha256.",
    )
    expected_diff_sha256: str = Field(
        ...,
        description="SHA-256 of the approved unified diff (from GET /diff).",
    )
    remove_worktree: bool = Field(
        True,
        description="Remove the arc worktree after successful integration.",
    )


class IntegrateResponse(BaseModel):
    """Envelope returned by ``integrate_op`` / ``land_op`` (extra fields allowed)."""

    model_config = ConfigDict(extra="allow")

    integration_id: str
    status: str
    reason_code: str = ""
    reason: str = ""
    committed: bool = False
    commit_sha: str = ""


class LandRequest(BaseModel):
    """Body for ``POST /land`` — commit stage + integrate in one gate slot."""

    model_config = ConfigDict(extra="forbid")

    arc: str = Field(..., description="Plan slug; worktree branch must be arc/<arc>.")
    phase: str = Field(..., description="Phase label for audit events.")
    worktree_path: str = Field(..., description="Absolute path to the arc worktree.")
    approval: str = Field(
        ...,
        description="Operator approval bound to expected_diff_sha256.",
    )
    expected_diff_sha256: str = Field(
        ...,
        description="SHA-256 of the approved unified diff (from GET /diff).",
    )
    commit_message: str = Field(
        "",
        description="Commit message for the arc-commit stage; required when dirty.",
    )
    remove_worktree: bool = Field(
        True,
        description="Remove the arc worktree after successful land.",
    )


class StatusResponse(BaseModel):
    """Read-only worktree status probe."""

    worktree_path: str
    branch: str = ""
    dirty: bool = False
    status: str = Field("ok", description="ok | rejected")
    reason_code: str = ""
    reason: str = ""


class DiffStatFile(BaseModel):
    """Per-file insertion/deletion counts from ``git diff --numstat``."""

    path: str
    insertions: int = 0
    deletions: int = 0
    binary: bool = Field(False, description="True when numstat reported '-' (binary).")


class DiffStat(BaseModel):
    """Compact change-set summary — aggregate totals + per-file numstat."""

    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0
    files: list[DiffStatFile] = Field(default_factory=list)


class DiffResponse(BaseModel):
    """Diff envelope + fingerprint for approval binding.

    Always carries ``diff_sha256``, ``diffstat``, ``branch``, and
    ``includes_uncommitted``. By default the full unified ``diff`` body is
    included (legacy callers); pass ``include_full_diff=false`` for compact-only
    (friction 11511).
    """

    worktree_path: str
    diff: str = ""
    diff_sha256: str = ""
    diffstat: DiffStat | None = None
    branch: str = ""
    path_filter: str = ""
    includes_uncommitted: bool = False
    full_diff_included: bool = Field(
        False,
        description="Echoes the caller's ``include_full_diff`` flag (not whether ``diff`` is non-empty).",
    )
    status: str = Field("ok", description="ok | rejected")
    reason_code: str = ""
    reason: str = ""
