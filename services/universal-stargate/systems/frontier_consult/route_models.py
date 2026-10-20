"""Admission gates split from route.py. Behavior unchanged."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from .densify_triage import DensityTriage
from .dispatch_thread_context import (
    validate_explicit_prompt_sources,
)

_REFUSED_PURPOSE = "review"
_REFUSED_ROLE = "reviewer"


def _refuse_review_identity(*, purpose: str | None, role: str | None) -> None:
    """Retired review identity is not a job. Validation fails; the proxy handler
    answers ``job_unknown`` (not ``job_retired``)."""
    if isinstance(purpose, str) and purpose.strip() == _REFUSED_PURPOSE:
        raise ValueError("purpose 'review' is not a registry job")
    if isinstance(role, str) and role.strip() == _REFUSED_ROLE:
        raise ValueError("role 'reviewer' is not a registry job")


# ---- dispatch-surface-split Phase 1: op-discriminated body models ----


class _DispatchCommon(BaseModel):
    """Shared fields across all op variants — not instantiated directly."""

    model_config = {"extra": "forbid"}

    system: str = ""
    reasoning_effort: str | None = None
    generation_options: dict[str, Any] | None = None
    max_tool_turns: int | None = None
    transcript_id: str | None = None
    caller_agent: str | None = None
    timeout_seconds: int | None = Field(default=None, gt=0, le=86_400)
    bus_lifecycle: Literal["persistent", "ephemeral"] | None = None
    skills: list[str] | None = None
    server_tools: bool | None = None
    wake_lane: str | None = Field(
        default=None,
        description=(
            "Opt-in operator continuity root. A worker closeout pastes a "
            "pointer memo on this lane. Numeric thread id carrying role:root."
        ),
    )


class TeamDispatchGenerateBody(_DispatchCommon):
    """``team_dispatch`` with ``op="generate"`` — default bus thread delivery.

    API functional roles auto-provision an agent-bus thread and admit
    ``output_contract=thread`` (poll ``poll_hint``, not inline-only
    ``pipeline(op=result)``). Auto-dispatch seats (``cursor-sdk``) use
    ``seat=`` and the dedicated SDK orchestrator.

    ``dispatch_thread_id`` binds server-owned thread persistence on the
    team-dispatch pipeline (distinct from ``transcript_id`` provenance-only).
    On ``seat=cursor-sdk``, a standing (non-pending-empty) id is the **coord**
    parent — a sibling worker thread is minted. ``admit_pointer.loop_closure``
    refuses only unbounded same-thread **thread-latest** (``latest`` /
    review-child ``explicit_inline``). ``packet_path``, ``sidecar_ref``, and
    inline ``prompt=`` bind as ``explicit_external`` and are legal on a
    standing arc. Do not pre-mint ``lifecycle_state=pending`` as the common
    path.
    """

    op: Literal["generate"]
    role: str | None = None
    seat: str | None = None
    dispatch_thread_id: str | None = None
    model: str | None = None
    # Caller inline-intent knob. ``None`` keeps the team default (tools-on for
    # tool-capable families); ``False`` requests an inline-only generation (no
    # client-side MCP loop and, for Anthropic, no server-side remote MCP),
    # closing the forced-remote-MCP hang (thread 1653).
    mcp: bool | None = None
    packet_path: str | None = None
    source_ref: str | None = None
    dispatch_lane: str | None = None
    # Inline prompt (SF1): bypass thread latch — prefer over bus-turn-only dispatch.
    prompt: str | None = None
    sidecar_ref: str | None = None
    prompt_sha256: str | None = None
    # Writer pipeline opt-in. ``pointer_only`` keeps prompt and harvest text
    # off agent-bus turns. Omitted leaves every other caller unchanged.
    bus_body: Literal["pointer_only"] | None = None
    # When set and packet_path is absent (job=implement|wrap), the server
    # materializes the six-block packet via resolve_source_ref_to_packet
    # (first-class wrap). Grammar: todo:/plan:/plan_phase:/agent-bus:/packet:.
    # Optional so intake owns job grammar (missing, retired, and op admission).
    job: str | None = None
    reuse_thread: str | None = None
    split_thread: bool = False
    density_triage: DensityTriage | None = None
    review_opt_out_reason_code: (
        Literal[
            "routine_single_subsystem",
            "suggestion_only_first_pass",
            "cost_exceeds_false_negative_risk",
        ]
        | None
    ) = None
    auto_review_child: bool | None = None
    model_knobs: dict[str, str] | None = None
    spawn_review_provenance: Literal["generate_review_child"] | None = None
    cost_intent: Literal["deliberate_high_cost"] | None = None
    suppress_cost_warning: bool = False
    cost_intent_reason: str | None = None
    nest_under: str | None = None
    # SDK agent-plane resume: terminal parent dispatch_id. Requires reuse_thread
    # (same mailbox). XOR nest_under. GIW sole eligibility authority.
    resume_of: str | None = None
    # Stamped by the stdio bridge only when resume_of is set. GIW refuses when
    # this id is the live write-lease holder (CURSOR_RESUME_BEHIND_CALLER).
    caller_dispatch_id: str | None = None
    # Required on top-level cursor-sdk generate. Omit only for nest_under or
    # resume_of inherit. Missing → 422 lane_required. wrap is exempt.
    lane: Literal["A", "B"] | None = None
    # Allowlisted satellite name; omit = hub ULG git identity.
    workspace: str | None = None
    read_only: bool = False
    refuse_if_lease_held: bool = False
    prompt_turn_number: int | None = None
    prompt_bind_mode: str | None = None
    # CDP registry / mission tag (model=cdp/… only). Omitted → infer
    # (sonnet → produce, else ask). operator-proxy|mission triggers
    # skill-chip inject on the satellite.
    purpose: str | None = None
    # Admit key for the CDP skill floor. operator-proxy|mission|ask.
    # Unknown present values (operator_proxy) are session_unknown at intake.
    session: str | None = None
    # Chrome-host lineage (model=cdp/… only). Distinct from purpose retain tag.
    mission_kind: str | None = None
    # Bus private-request parent lane (hop/side parent). Not SDK nest_under.
    parent_thread: str | None = None
    # Hop only (mission_kind=hop): CSE registration of the seat being replaced.
    # The external-gate check excludes that seat's live generate and still
    # refuses any other live gate on the lane. Ignored unless mission_kind=hop.
    predecessor_registration_id: str | None = None
    # Caller-reserved worker dispatch_id (seat=cursor-sdk). GIW hop and bus-watch
    # play callers claim a stop's one admit slot under this id before POSTing, so
    # the admitted ledger row must carry the same id. Omit to let the server mint.
    dispatch_id: str | None = None
    # Conductor hop successor wire (bind §2.6.4): triplet admitted together on GIW.
    hop_from: str | None = None
    hop_seq: int | None = Field(default=None, ge=0)
    hop_reason: (
        Literal[
            "spawn",
            "planned",
            "crash",
            "silent",
            "watchdog",
            "park_harvest",
            "cdp_probe_indeterminate",
            "producer_harvest",
        ]
        | None
    ) = None
    work_key: str | None = None
    force: bool = False
    force_reason: str | None = None
    # thread / subject MUST NOT appear — extra="forbid" rejects any caller that
    # supplies them (schema-level enforcement per Phase 0 contract).

    @model_validator(mode="after")
    def _refuse_retired_review_identity(self) -> Self:
        _refuse_review_identity(purpose=self.purpose, role=self.role)
        return self

    @model_validator(mode="after")
    def _validate_hop_triplet(self) -> Self:
        hop_triplet = (self.hop_seq, self.hop_from, self.hop_reason)
        if any(v is not None for v in hop_triplet) and not all(
            v is not None for v in hop_triplet
        ):
            raise ValueError(
                "hop_seq, hop_from, and hop_reason must be supplied together"
            )
        return self

    @model_validator(mode="after")
    def _validate_inline_prompt_sources(self) -> Self:
        validate_explicit_prompt_sources(
            contract=self.job,
            packet_path=self.packet_path,
            source_ref=self.source_ref,
            prompt=self.prompt,
            sidecar_ref=self.sidecar_ref,
        )
        return self

    @model_validator(mode="after")
    def _require_dispatch_thread_id_unless_wrap(self) -> Self:
        if self.job != "wrap":
            has_explicit_prompt = any(
                value is not None and str(value).strip()
                for value in (self.packet_path, self.prompt, self.sidecar_ref)
            )
            if not has_explicit_prompt and (
                not self.dispatch_thread_id or not self.dispatch_thread_id.strip()
            ):
                raise ValueError(
                    "dispatch_thread_id is required when contract is not wrap"
                )
        return self

    @model_validator(mode="after")
    def _validate_wrap_contract_inputs(self) -> Self:
        if self.job != "wrap":
            return self
        if self.packet_path is not None:
            raise ValueError("wrap_with_packet_path")
        if self.source_ref is None:
            raise ValueError("wrap_requires_source_ref")
        if self.density_triage is not None:
            raise ValueError("wrap_field_not_applicable:density_triage")
        if self.review_opt_out_reason_code is not None:
            raise ValueError("wrap_field_not_applicable:review_opt_out_reason_code")
        if self.auto_review_child:
            raise ValueError("wrap_field_not_applicable:auto_review_child")
        return self


class TeamDispatchClaimBody(_DispatchCommon):
    """``team_dispatch`` op ``claim`` binds one house holder to a work_key.

    ``thread`` becomes the GIW ``thread_id``. ``caller_agent`` is the holder;
    the body has no separate free-text holder field. ``ttl_s`` is optional.
    """

    op: Literal["claim"]
    work_key: str
    thread: str
    ttl_s: int | None = None


class TeamDispatchClaimReleaseBody(_DispatchCommon):
    """``team_dispatch`` op ``claim_release`` ends the caller's house claim.

    ``outcome`` is completed, failed, or withdrawn. A missing claim is 404
    and a claim held by someone else is 409.
    """

    op: Literal["claim_release"]
    claim_id: str
    outcome: Literal["completed", "failed", "withdrawn"]


class TeamDispatchOccupancyBody(_DispatchCommon):
    """``team_dispatch`` op ``occupancy`` reads who holds a work_key.

    The GIW projection is occupancy, not progress: no landed or done field.
    ``work_key`` uses the same D4 grammar as generate admits.
    """

    op: Literal["occupancy"]
    work_key: str


class TeamDispatchLedgerBody(_DispatchCommon):
    """``team_dispatch`` op ``ledger`` returns recent dispatches for one work_key."""

    op: Literal["ledger"]
    work_key: str
    limit: int = Field(default=5, ge=1, le=10)
    window_hours: int = Field(default=24, ge=1, le=168)


class TeamDispatchSteerBody(BaseModel):
    """``team_dispatch`` with ``op="steer"`` — thin GIW park/inject relay."""

    model_config = {"extra": "forbid"}

    op: Literal["steer"]
    dispatch_id: str
    steer: Literal["park_for_restart", "cancel_discard", "inject"]
    reason: str
    actor: str | None = None
    directive: str | None = None
    ttl_s: int | None = None

    @model_validator(mode="after")
    def _validate_steer_fields(self) -> Self:
        if self.steer == "inject" and not (self.directive and self.directive.strip()):
            raise ValueError("directive is required when steer='inject'")
        return self


class TeamDispatchToThreadBody(_DispatchCommon):
    """``team_dispatch`` with ``op="to_thread"`` — result posted to agent-bus thread.

    ``role`` selects a ``role:{slug}`` execution contract.

    ``thread`` is the agent-bus delivery target. ``dispatch_thread_id`` is the
    cortex compaction key (orthogonal — do not conflate the two).
    """

    op: Literal["to_thread"]
    role: str
    dispatch_thread_id: str
    thread: str
    subject: str | None = None
    model: str | None = None
    # Caller inline-intent knob (see ``TeamDispatchGenerateBody.mcp``).
    mcp: bool | None = None
    job: str | None = None
    prompt: str | None = None
    sidecar_ref: str | None = None
    auto_review_child: bool | None = None
    read_only: bool = False
    spawn_review_provenance: Literal["generate_review_child"] | None = None
    cost_intent: Literal["deliberate_high_cost"] | None = None
    suppress_cost_warning: bool = False
    cost_intent_reason: str | None = None
    # result_delivery MUST NOT appear — derived from thread + role; extra="forbid"
    # rejects any caller that supplies it.

    @model_validator(mode="after")
    def _refuse_retired_review_identity(self) -> Self:
        _refuse_review_identity(purpose=None, role=self.role)
        return self

    @model_validator(mode="after")
    def _validate_inline_prompt_sources(self) -> Self:
        validate_explicit_prompt_sources(
            contract=self.job,
            packet_path=None,
            source_ref=None,
            prompt=self.prompt,
            sidecar_ref=self.sidecar_ref,
        )
        return self


# FastAPI resolves the union via the ``op`` discriminator key.
TeamDispatchBody = Annotated[
    TeamDispatchGenerateBody
    | TeamDispatchToThreadBody
    | TeamDispatchSteerBody
    | TeamDispatchClaimBody
    | TeamDispatchClaimReleaseBody
    | TeamDispatchOccupancyBody
    | TeamDispatchLedgerBody,
    Field(discriminator="op"),
]
