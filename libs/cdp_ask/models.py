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
    model: str = "opus-5"
    converse: bool = False
    no_project_uuid: bool = False
    project_uuid: str = ""
    ensure_cowork_auto: bool = True
    archive_path: str | None = None
    timeout_s: int = 360
    min_body: int = 40
    min_growth: int = 50
    delete_after: bool | None = None
    expected_size: Literal["small", "large", "auto"] = Field(
        default="auto",
        description=(
            "Expected deliverable size. ``large`` with ``harvest_source=auto`` "
            "attempts Cowork Output download before archive; ``small`` never "
            "forces Output download."
        ),
    )
    harvest_source: Literal["chat", "output-file", "auto"] = Field(
        default="auto",
        description=(
            "Submit-time harvest knob (distinct from poll ``harvest_provenance``). "
            "Operational default: ``auto`` or ``output-file`` (Cowork). "
            "``chat`` is wire-stub only — future Chat UI path; can cover small and "
            "large (cortex-direct) but untested; auth gates likely on cortex/life "
            "work; simple one-offs without cortex likely fine — ¬ production or "
            "skill guidance yet (spec: substrate-apis-cdp-cursor § Chat harvest "
            "stub). "
            "``output-file`` requires Output download (hard fail on miss); "
            "``auto`` tries Output then cortex-fs pointer; under ``expected_size=large`` "
            "refuses thin chat fallback (fail-closed)."
        ),
    )
    download_output: bool = Field(
        default=False,
        description=(
            "When true (or with ``expected_size=large``), attempt Cowork Output "
            "download into the archive path before ``content_proof``."
        ),
    )


class ExecutionSummary(BaseModel):
    """Compact execution row for list endpoints and internal store snapshots."""

    execution_id: str
    status: ExecutionStatus
    registration_id: str | None = None
    created_at: float
    updated_at: float


class SubmitProjectAskResponse(BaseModel):
    """202 submit acknowledgement — admission only, not a completed handoff.

    ``status=running`` with ``terminal=false`` means the satellite accepted the
    work and minted ``execution_id``. It does **not** mean a Cowork window is
    live or that the CDP seat has spoken. Consumers must poll (or wait on the
    bus) until a terminal outcome; relaying this envelope as a completed
    handoff is the 2026-07-31 b7ea437d failure class.
    """

    execution_id: str
    status: ExecutionStatus
    registration_id: str | None = None
    terminal: bool = Field(
        default=False,
        description=(
            "Always false on submit. Admission ≢ arrival — do not relay "
            "status=running as a completed handoff."
        ),
    )
    phase: Literal["admitted"] = Field(
        default="admitted",
        description=(
            "Submit is phase=admitted only. Window liveness and first CDP reply "
            "are later poll/bus observations."
        ),
    )
    handoff_status: Literal["awaiting_first_reply"] = Field(
        default="awaiting_first_reply",
        description=(
            "Mirrors Stargate CDP generate honesty: the handoff is open until "
            "the CDP seat posts a reply or FAILED turn."
        ),
    )


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
    harvest_provenance: (
        Literal["output-file", "cortex-uri", "chat", "chat-large"] | None
    ) = Field(
        default=None,
        description=(
            "Resolution outcome provenance (distinct from submit ``harvest_source``). "
            "Present on successful terminals only; ``null`` on non-success. "
            "When present: ``output-file`` | ``cortex-uri`` | ``chat`` | ``chat-large``. "
            "Never ``ok=true`` with ``harvest_provenance=chat`` when "
            "``expected_size=large`` (auto fail-closed). ``chat-large`` marks the "
            "measured escape from that guard: Output and cortex-uri both missed but "
            "the scraped body exceeded ``THIN_CHAT_BODY_MAX_CHARS``, so it is a "
            "transcript rather than a completion card. Audit ``chat-large`` archives "
            "when an Output file was genuinely expected."
        ),
    )
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
            "Unix epoch when the satellite first attested confirmed CDP turn idle after the "
            "page_liveness gate (sustained idle after activity, idle-after-growth with body "
            "delta past min_body, or ESCAPE_IDLE_SAMPLES failsafe) — not the first quiet "
            "harvest sample. Required conjunct for content_proof; idle alone does not advance "
            "R-admit or trigger delete_after. Worst-case added latency on paths that never "
            "flip seen_active is ESCAPE_IDLE_SAMPLES × poll_ms (~60s at poll_ms=500)."
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


class FollowupCandidateInfo(BaseModel):
    """One attached-lane CSE match returned on ``ambiguous_identity``."""

    registration_id: str
    chat_url: str
    holder: str
    purpose: str | None = None


class FollowupProjectAskRequest(BaseModel):
    """Warm paste into a live retained Cowork CSE on an attached CDP lane."""

    chat_url: str | None = None
    registration_id: str | None = None
    execution_id: str | None = None
    purpose: str | None = None
    prompt_text: str | None = None
    prompt_uri: str | None = None
    prompt_path: str | None = None
    timeout_s: int = 60
    reattach: bool = False
    retain_lane: bool = False


class FollowupProjectAskResponse(BaseModel):
    """Synchronous paste-proof result — ``ok=true`` only when ``send_verified``."""

    ok: bool
    url: str | None = None
    registration_id: str | None = None
    execution_id: str | None = None
    pasted_at: float | None = None
    send_verified: bool = False
    streaming_at_paste: bool | None = None
    error: str | None = None
    detail: str | None = None
    candidates: list[FollowupCandidateInfo] | None = None
    reattach_used: bool = False
    lane_created: bool = False
