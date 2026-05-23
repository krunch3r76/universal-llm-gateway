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

from pydantic import BaseModel, ConfigDict, Field

DispatchState = Literal["pending", "running", "succeeded", "failed", "cancelled"]


class GrokbuildDispatchRequest(BaseModel):
    """Async build dispatch request body — mirrors ``dispatch_op`` kwargs."""

    model_config = ConfigDict(extra="forbid")

    cwd: str = Field(..., description="Absolute path to the dispatch worktree.")
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
    timeout_seconds: int | None = Field(None, description="Override tier preset.")
    tier: str = Field("thorough", description="Tier preset key.")
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


class GrokbuildDispatchAccepted(BaseModel):
    """HTTP 202 response body for ``POST /dispatches``."""

    dispatch_id: str
    status_url: str
    events_url: str
    state: DispatchState = "pending"


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
