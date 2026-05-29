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
    """Envelope returned by ``integrate_op`` (extra fields allowed)."""

    model_config = ConfigDict(extra="allow")

    integration_id: str
    status: str
    reason_code: str = ""
    reason: str = ""


class StatusResponse(BaseModel):
    """Read-only worktree status probe."""

    worktree_path: str
    branch: str = ""
    dirty: bool = False
    status: str = Field("ok", description="ok | rejected")
    reason_code: str = ""
    reason: str = ""


class DiffResponse(BaseModel):
    """Unified diff + fingerprint for approval binding."""

    worktree_path: str
    diff: str = ""
    diff_sha256: str = ""
    path_filter: str = ""
    status: str = Field("ok", description="ok | rejected")
    reason_code: str = ""
    reason: str = ""
