"""ImplementSpec v1 schema and canonical hash helper."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SourceKind(StrEnum):
    TODO = "todo"
    PLAN = "plan"
    PLAN_PHASE = "plan_phase"
    PACKET = "packet"
    AGENT_BUS = "agent-bus"
    TASK = "task"  # provenance-only; non-gating; not a materialisable dispatch source


class ReadinessState(StrEnum):
    READY = "ready"
    GATED = "gated"


class OrchestrationMode(StrEnum):
    SINGLE = "single"
    COORDINATOR = "coordinator"


class ExecutorStyle(StrEnum):
    REASONING = "reasoning"
    MECHANICAL = "mechanical"


class CloseoutAdapterKind(StrEnum):
    TODO = "todo"
    PLAN = "plan"
    PLAN_PHASE = "plan_phase"
    PACKET = "packet"
    AGENT_BUS = "agent-bus"
    MIXED = "mixed"


class CloseoutStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    GATED = "gated"


NO_RUN_DEGRADED_REASONS: frozenset[str] = frozenset(
    {"empty_assistant_turn", "zero_tool_calls", "empty_terminal_output"}
)


class WorkOutcome(StrEnum):
    SHIPPED = "shipped"
    NOT_SHIPPED = "not_shipped"
    UNVERIFIED = "unverified"
    CHECKS_FAILED = "checks_failed"


class SourceVersion(BaseModel):
    content_hash: str | None = None
    packet_sha256: str | None = None
    deck_sha256: str | None = None


class Source(BaseModel):
    source_ref: str
    canonical_ref: str
    parent_ref: str | None = None
    selector: str | None = None
    source_kind: SourceKind
    # Human narrative path for corpus display only; drift-bound via content_hash.
    source_uri: str | None = None
    source_version: SourceVersion = Field(default_factory=SourceVersion)


class Intent(BaseModel):
    summary: str
    description: str | None = None


class Scope(BaseModel):
    files_expected: list[str] = Field(default_factory=list)
    bounded: bool = True
    # Verbatim phase-deck text carried for the plan_phase corpus embed. Elided
    # from implement_spec_hash (the compact source_version.deck_sha256 is the
    # hash-bound fingerprint, computed from the same normalized bytes) — see
    # implement_spec_hash below + spec plan-deck-handoff-packet-adapter §6/§15.
    deck_body: str | None = None


class Readiness(BaseModel):
    state: ReadinessState
    gated_reason: str | None = None
    freshness_checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _gated_requires_reason(self) -> Readiness:
        if self.state == ReadinessState.GATED and not self.gated_reason:
            msg = "gated_reason required when readiness.state is gated"
            raise ValueError(msg)
        return self


class RoutingDerivation(BaseModel):
    mode_rule: str
    style_rule: str


class Routing(BaseModel):
    orchestration_mode: OrchestrationMode
    executor_style: ExecutorStyle
    checkpoint_required: bool = False
    derivation: RoutingDerivation
    requested_execution_mode: str | None = None


class RouteContract(BaseModel):
    """Server-owned dispatch semantics — authoritative over lead-seat prose."""

    policy_source: str
    policy_version: str
    dispatch_kind: str
    transport: str
    autonomy: Literal["auto_executed", "manual_pickup"]
    operator_pickup_required: bool
    lead_claim_authority: str


class Acceptance(BaseModel):
    criteria: list[str]


class Closeout(BaseModel):
    adapter: CloseoutAdapterKind
    bus_thread: str | None = None


class ReviewAttestation(BaseModel):
    required: bool = False
    risk_tier: Literal["mechanical", "material", "critical"] = "mechanical"
    spec_hash: str | None = None
    author_family: str = "claude"
    reviewer_family: str | None = None
    reviewer_model: str | None = None
    review_execution_id: str | None = None
    review_artifact_uri: str | None = None
    disposition: Literal[
        "pass",
        "pass_with_conditions",
        "blocked",
        "pending",
        "missing",
    ] = "missing"
    unresolved_blocker_ids: list[str] = Field(default_factory=list)
    resolved_blocker_map: dict[str, str] = Field(default_factory=dict)


class Provenance(BaseModel):
    implement_spec_hash: str | None = None
    review_attestation: ReviewAttestation | None = None
    generated_from: str = "implement_admission_v1"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    normalizer_version: str = "1.0.0"


class ImplementSpec(BaseModel):
    schema_version: Literal[1] = 1
    source: Source
    intent: Intent
    scope: Scope = Field(default_factory=Scope)
    readiness: Readiness
    skills: list[str] = Field(default_factory=list)
    routing: Routing | None = None
    route_contract: RouteContract | None = None
    acceptance: Acceptance
    closeout: Closeout
    provenance: Provenance = Field(default_factory=Provenance)

    @model_validator(mode="after")
    def _readiness_routing_consistency(self) -> ImplementSpec:
        if self.readiness.state == ReadinessState.GATED:
            if self.routing is not None:
                msg = "routing must be absent when readiness.state is gated"
                raise ValueError(msg)
        elif self.routing is None:
            msg = "routing required when readiness.state is ready"
            raise ValueError(msg)
        return self


def _canonical_default(o: object) -> object:
    from datetime import date, datetime
    from enum import Enum

    if isinstance(o, Enum):
        return o.value
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, (set, frozenset)):
        return sorted(
            o, key=lambda x: json.dumps(x, sort_keys=True, separators=(",", ":"))
        )
    raise TypeError(
        f"implement_spec_hash: non-canonical type {type(o)!r} reached the hash encoder; "
        "classify the field as hash-participating with a deterministic encoding or elide it as volatile."
    )


def implement_spec_hash(spec: ImplementSpec) -> str:
    """SHA256 over canonical JSON with provenance.implement_spec_hash elided."""
    payload = spec.model_dump()
    payload["provenance"]["implement_spec_hash"] = None
    payload["provenance"]["review_attestation"] = None
    payload.pop("route_contract", None)
    # Volatile at normalize()/materialize() time — must not affect drift-guard stability.
    payload["provenance"]["created_at"] = None
    if payload.get("readiness") is not None:
        payload["readiness"]["freshness_checked_at"] = None
    # deck_body is never hashed by bulk (deck_sha256 is the fingerprint). Pop it
    # entirely so a deck-less spec hashes identically to the pre-deck schema —
    # no spurious drift for todo/plan/packet specs.
    if payload.get("scope") is not None:
        payload["scope"].pop("deck_body", None)
    # deck_sha256 binds the deck fingerprint when present; pop when None so
    # deck-less specs are unaffected by the new field.
    source = payload.get("source")
    if source is not None:
        source.pop("source_uri", None)
        source_version = source.get("source_version")
        if source_version is not None and source_version.get("deck_sha256") is None:
            source_version.pop("deck_sha256", None)
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=_canonical_default
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def finalize_spec(
    spec: ImplementSpec,
    *,
    contract: str | None = None,
    seat: str | None = None,
    role: str | None = None,
    transport: str = "team_dispatch",
) -> ImplementSpec:
    """Attach implement_spec_hash; optionally attach route_contract at this chokepoint."""
    from implement_admission.routing import with_route_contract

    h = implement_spec_hash(spec)
    updated = spec.model_copy(
        update={
            "provenance": spec.provenance.model_copy(update={"implement_spec_hash": h})
        }
    )
    if contract is not None:
        updated = with_route_contract(
            updated,
            contract=contract,
            seat=seat,
            role=role,
            transport=transport,
        )
    return updated
