"""Pydantic models for the async build dispatch surface (Phase B).

Request mirrors the kwargs of :func:`grokbuild.dispatch.dispatch_op`; the
worker route layer translates the model into the lib call without
duplicating validation (the lib's :func:`validate_dispatch` is the source
of truth for parameter ranges and structural rules).

Response models surface the tracker's snapshot — they are NOT the
canonical envelope (the canonical envelope is delivered by
``GET /dispatches/{id}/result`` and remains unchanged from Phase A.3).
"""

from __future__ import annotations

from typing import Any, Literal

from grokbuild.constants import DISPATCH_MODEL_ID
from pydantic import BaseModel, ConfigDict, Field, model_validator

DispatchState = Literal["pending", "running", "succeeded", "failed", "cancelled"]


class GrokbuildDispatchRequest(BaseModel):
    """Async build dispatch request body — mirrors ``dispatch_op`` kwargs."""

    model_config = ConfigDict(extra="forbid")

    cwd: str | None = Field(
        None,
        description=(
            "Absolute path to the dispatch worktree. Optional when "
            "source_repo is supplied."
        ),
    )
    source_repo: str | None = Field(
        None,
        description=(
            "Repo-name alias or absolute path; worker resolves to host "
            "projects root before dispatch."
        ),
    )
    prompt: str = Field(..., description="Prompt text passed to grok.")
    mode: Literal["read_only", "edit"] = Field(
        "read_only", description="Dispatch mode (validator enforces semantics)."
    )
    system_context: str | None = Field(None, description="Optional system prompt.")
    model: str | None = Field(None, description="Grok model id; None → lib default.")
    session_id: str | None = Field(None, description="Resume session id when set.")
    continue_recent: bool = Field(
        False, description="Always False in production; validator rejects True."
    )
    output_format: str = Field("streaming-json", description="grok output format.")
    timeout_seconds: int | None = Field(
        None,
        description=(
            "Subprocess wall-clock limit (seconds). Omitted → 3600. "
            "0 → no limit. Otherwise 1–86400."
        ),
    )
    tier: str = Field(
        "thorough",
        description=(
            "Tier preset key. When model is omitted, selects the default model "
            "for both mcp=True (CLI subprocess) and mcp=False (API) paths."
        ),
    )
    reasoning_effort: str | None = Field(None)
    effort: str | None = Field(None)
    check: bool | None = Field(None)
    no_subagents: bool = Field(False)
    disable_web_search: bool = Field(False)
    max_turns: int | None = Field(None)
    best_of_n: int | None = Field(None)
    resume_strict: bool = Field(False)
    # MQ3 (G7): audit metadata propagated from the calling session.
    seat: str | None = Field(None, description="Caller seat slug for audit trail.")
    role: str | None = Field(None, description="Caller role slug for audit trail.")
    recursion_depth: int | None = Field(
        None, description="MQ3 dispatch chain depth; worker rejects if > 2."
    )
    # Phase D: MCP path selector.
    # True (default) → grok CLI subprocess with dispatch bearer (grok-build-dispatch seat).
    # False → direct LLM API call via Stargate; no subprocess, no MCP inside dispatch.
    mcp: bool = Field(True, description="MCP-enabled path selector.")

    @model_validator(mode="after")
    def _validate_cwd_or_source_repo(self) -> GrokbuildDispatchRequest:
        if not self.cwd and not self.source_repo:
            raise ValueError("one of cwd or source_repo is required")
        return self

    @model_validator(mode="after")
    def _validate_mcp_compatibility(self) -> GrokbuildDispatchRequest:
        """Reject grok-CLI-only fields when mcp=False (api path).

        The api path (mcp=False) is a direct LLM API call via Stargate —
        no subprocess, no MCP tooling, no agent loop. Fields that only
        make sense for the grok CLI subprocess MUST NOT be set when
        mcp=False; silently dropping them would rewrite caller intent
        (see master @ cab52fa7 session review, finding F4).
        """
        if not self.mcp:
            incompatible: list[str] = [
                "mcp=False (api path disabled; grokbuild admits only "
                f"model={DISPATCH_MODEL_ID!r} via CLI subprocess)"
            ]
            if self.mode == "edit":
                incompatible.append("mode='edit' (api path cannot edit; no subprocess)")
            if self.continue_recent:
                incompatible.append("continue_recent=True (grok CLI flag only)")
            if self.reasoning_effort is not None:
                incompatible.append("reasoning_effort (grok CLI flag only)")
            if self.effort is not None:
                incompatible.append("effort (grok CLI flag only)")
            if self.check is not None:
                incompatible.append("check (grok CLI flag only)")
            if self.no_subagents:
                incompatible.append("no_subagents=True (grok CLI flag only)")
            if self.disable_web_search:
                incompatible.append("disable_web_search=True (grok CLI flag only)")
            if self.max_turns is not None:
                incompatible.append("max_turns (grok CLI flag only)")
            if self.best_of_n is not None:
                incompatible.append("best_of_n (grok CLI flag only)")
            if self.resume_strict:
                incompatible.append("resume_strict=True (requires CLI session)")
            if incompatible:
                raise ValueError(
                    "mcp=False (api path) is incompatible with: "
                    + "; ".join(incompatible)
                )
        return self


class GrokbuildDispatchAccepted(BaseModel):
    """HTTP 202 response body for ``POST /dispatches``."""

    dispatch_id: str
    status_url: str
    events_url: str
    state: DispatchState = "pending"
    # Common-channel pointer (decision:build-result-common-channel): where the
    # fs-reachable spool WILL be once the dispatch reaches terminal state. Keys:
    # sandbox, result_dir, signals_path, envelope_path, sidecar_path.
    result_ref: dict[str, Any] = Field(default_factory=dict)


class GrokbuildDispatchStatus(BaseModel):
    """HTTP 200 response body for ``GET /dispatches/{id}``."""

    dispatch_id: str
    state: DispatchState
    started_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    progress_summary: str = ""
    last_event: dict[str, Any] | None = None
    result_available: bool = False
    pid: int | None = None
    exit_code: int | None = None
    error: str | None = None


class GrokbuildDispatchCancelled(BaseModel):
    """HTTP 200 response body for ``DELETE /dispatches/{id}``."""

    dispatch_id: str
    state: DispatchState
    signal_used: str
    reason: str = "operator_cancel"
