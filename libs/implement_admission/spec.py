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


class SourceVersion(BaseModel):
    content_hash: str | None = None
    packet_sha256: str | None = None


class Source(BaseModel):
    source_ref: str
    canonical_ref: str
    parent_ref: str | None = None
    selector: str | None = None
    source_kind: SourceKind
    source_version: SourceVersion = Field(default_factory=SourceVersion)


class Intent(BaseModel):
    summary: str
    description: str | None = None


class Scope(BaseModel):
    files_expected: list[str] = Field(default_factory=list)
    bounded: bool = True


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


class Acceptance(BaseModel):
    criteria: list[str]


class Closeout(BaseModel):
    adapter: CloseoutAdapterKind
    bus_thread: str | None = None


class Provenance(BaseModel):
    implement_spec_hash: str | None = None
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


def implement_spec_hash(spec: ImplementSpec) -> str:
    """SHA256 over canonical JSON with provenance.implement_spec_hash elided."""
    payload = spec.model_dump()
    payload["provenance"]["implement_spec_hash"] = None
    # Volatile at normalize()/materialize() time — must not affect drift-guard stability.
    payload["provenance"]["created_at"] = None
    if payload.get("readiness") is not None:
        payload["readiness"]["freshness_checked_at"] = None
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def finalize_spec(spec: ImplementSpec) -> ImplementSpec:
    """Attach implement_spec_hash to provenance."""
    h = implement_spec_hash(spec)
    updated = spec.model_copy(
        update={"provenance": spec.provenance.model_copy(update={"implement_spec_hash": h})}
    )
    return updated
