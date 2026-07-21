"""Request/response models for the CDP project-ask satellite HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ExecutionStatus = Literal["pending", "running", "completed", "failed", "aborted"]

CompletionPhase = Literal[
    "running",
    "turn_idle",
    "content_proof",
    "archiving",
    "terminal",
    "failed",
]

StallStage = Literal[
    "completion_detection",
    "archive_write",
    "mark_terminal",
    "post_terminal_poll",
    "unknown",
]


def classify_stall_stage(error: str | None) -> StallStage:
    """Map harness error text to a poll-visible stall_stage."""
    if not error:
        return "unknown"
    low = error.lower()
    if any(
        token in low
        for token in (
            "timed out incomplete",
            "harvestincomplete",
            "overloaded",
            "error_banner",
            "hit a limit",
        )
    ):
        return "completion_detection"
    if "archive" in low:
        return "archive_write"
    if error in {"cancelled", "aborted"}:
        return "mark_terminal"
    return "unknown"


class SubmitProjectAskRequest(BaseModel):
    """Inbound submit body for a sealed CDP project-ask execution on the satellite."""
    prompt_text: str | None = None
    prompt_uri: str | None = None
    prompt_path: str | None = None
    holder: str = Field(default="cdp-ask-satellite")
    purpose: str = "ask"
    model: str = "opus-4.8"
    converse: bool = False
    no_project_uuid: bool = False
    project_uuid: str = ""
    ensure_cowork_auto: bool = True
    archive_path: str | None = None
    timeout_s: int = 360
    min_body: int = 40
    min_growth: int = 50
    delete_after: bool | None = None


class ExecutionSummary(BaseModel):
    """Compact execution row for list endpoints and internal store snapshots."""
    execution_id: str
    status: ExecutionStatus
    registration_id: str | None = None
    created_at: float
    updated_at: float


class SubmitProjectAskResponse(BaseModel):
    """202 submit acknowledgement with execution_id for client-side polling."""
    execution_id: str
    status: ExecutionStatus
    registration_id: str | None = None


class ExecutionPollResponse(BaseModel):
    """Poll-plane status for MCP and path-sim consumers including dual-completion ladder fields.

    ``completion_phase`` exposes running → turn_idle → content_proof → archiving →
    terminal | failed. Consumer invariants: ``turn_idle`` alone never advances R-admit;
    ``content_proof`` is not terminal and must not trigger delete_after; ``failed`` is
    not ``running`` and carries ``stall_stage`` on failure terminals.
    """
    execution_id: str
    status: ExecutionStatus
    registration_id: str | None = None
    ok: bool | None = None
    archive_uri: str | None = None
    body: str | None = None
    body_len: int | None = None
    url: str | None = None
    project_uuid: str | None = None
    project_url: str | None = None
    model: dict[str, Any] | None = None
    attested_model: str | None = None
    error: str | None = None
    delete_after: dict[str, Any] | None = None
    results: list[dict[str, Any]] | None = None
    completion_phase: CompletionPhase = Field(
        default="running",
        description=(
            "Dual-completion ladder rung exposed on every poll. Enum semantics: "
            "`running` — harness in flight; `turn_idle` — CDP turn idle (≠ advance-eligible "
            "alone); `content_proof` — durable sidecar + idle attested (≠ terminal, must not "
            "trigger delete_after); `archiving` — archive_harvest in progress; `terminal` — "
            "successful mark_terminal with archive_uri; `failed` — terminal failure lane "
            "(≠ running). Consumer invariants: turn_idle alone never advances R-admit; "
            "content_proof permits advance only after consumer fs-read + sha re-verify; "
            "failed must not be masked as running."
        ),
    )
    content_proof_uri: str | None = Field(
        default=None,
        description=(
            "cortex:// URI of the prompt-bound durable sidecar when completion_phase is "
            "content_proof or later. Consumers must fs-read and re-compute sha256 locally — "
            "do not trust this field alone for advance or cleanup."
        ),
    )
    content_proof_sha256: str | None = Field(
        default=None,
        description=(
            "Satellite-computed sha256:… digest of content_proof_uri at detection time. "
            "Advisory only — R-admit / Stage-B advance requires an independent consumer "
            "fs-read + sha re-verify before acting."
        ),
    )
    turn_idle_at: float | None = Field(
        default=None,
        description=(
            "Unix epoch when the satellite first attested CDP turn idle (no Stop/streaming/"
            "tool_pause). Required conjunct for content_proof; idle alone does not advance "
            "R-admit or trigger delete_after."
        ),
    )
    stall_stage: StallStage | None = Field(
        default=None,
        description=(
            "Best-effort failure locus when completion_phase=failed and status=failed. "
            "Enum: `completion_detection` — wait/harvest/banner timeout; `archive_write` — "
            "archive_harvest or path failure; `mark_terminal` — runner/satellite exception; "
            "`post_terminal_poll` — late poll mismatch; `unknown` — unclassified. "
            "Non-null on failed terminals so consumers distinguish stall from dual-completion lag."
        ),
    )
    streaming: bool | None = Field(
        default=None,
        description=(
            "Advisory Cowork window liveness mirroring harvest_assistant ``streaming``. "
            "Populated only while status=running and completion_phase=running; null otherwise. "
            "Does not affect dual-completion ladder semantics."
        ),
    )
    stop: bool | None = Field(
        default=None,
        description=(
            "Advisory Cowork window liveness mirroring harvest_assistant ``stop``. "
            "Populated only while status=running and completion_phase=running; null otherwise. "
            "Does not affect dual-completion ladder semantics."
        ),
    )
    tool_pause: bool | None = Field(
        default=None,
        description=(
            "Advisory Cowork window liveness mirroring harvest_assistant ``tool_pause``. "
            "Populated only while status=running and completion_phase=running; null otherwise. "
            "Does not affect dual-completion ladder semantics."
        ),
    )
    liveness_observed_at: float | None = Field(
        default=None,
        description=(
            "Unix epoch when the satellite last sampled Cowork window liveness via the "
            "content-proof watcher (~2s cadence). Populated only while status=running and "
            "completion_phase=running; null otherwise. Advisory only — does not gate "
            "turn_idle, content_proof, or stall_stage."
        ),
    )


class AbortExecutionResponse(BaseModel):
    """Abort attestation row describing whether the CDP lane was stopped and released."""
    execution_id: str
    status: ExecutionStatus
    aborted: bool
    attested: bool = False
    still_attached: bool | None = None
    abort_outcome: str = ""
    stop_clicked: bool | None = None
