"""Request and response models for the public ``/v1/cse-session/`` routes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

AckClass = Literal["typed_ack", "ordinary_content", "no_proof"]
HarvestOutcome = Literal[
    "harvested",
    "no_reply_yet",
    "streaming",
    "incomplete_dom",
    "unauthenticated",
    "not_attached",
    "dormant",
    "conflict",
    "unreachable",
]
HarvestSource = Literal["chat", "output-file", "auto"]
TurnSource = Literal["cse-dom", "output-file", "archive", "bus-fallback"]
PasteEnvelope = Literal["free", "stand_down", "page"]
MinReceipt = Literal["dom_paste", "dom_committed", "human_visible"]


class CseSessionTurn(BaseModel):
    """One harvested CSE turn with explicit content provenance."""

    author: str
    timestamp: str | None = None
    text: str
    source: TurnSource
    ordinal: int | None = None


class ProvenanceQuery(BaseModel):
    """Identity keys for provenance read — all optional; omitted returns candidates."""

    chat_url: str | None = None
    registration_id: str | None = None
    execution_id: str | None = None
    predecessor_registration_id: str | None = None
    successor_registration_id: str | None = None


class ProvenanceResponse(BaseModel):
    """Public provenance projection with separate claim and proven fields."""

    state: str
    host_state: str | None = None
    lineage_state: str | None = None
    evidence_class: str | None = None
    attribution_source: str | None = None
    chat_url: str | None = None
    registration_id: str | None = None
    execution_id: str | None = None
    lane_thread_claim: str | None = None
    lane_thread_proven: str | None = None
    parent_thread_claim: str | None = None
    parent_thread_proven: str | None = None
    lane_role_claim: str | None = None
    lane_role_proven: str | None = None
    association_id: int | None = None
    observed_at: float | None = None
    freshness: dict[str, float | None] | None = None
    reason: str | None = None
    candidates: list[dict[str, Any]] | None = None
    is_predecessor: bool | None = None
    is_successor: bool | None = None
    same_lane: bool | None = None


class HarvestRequest(BaseModel):
    """Bounded read-only harvest — no paste, submit, or Chrome relaunch."""

    chat_url: str | None = None
    registration_id: str | None = None
    execution_id: str | None = None
    limit: int = Field(default=10, ge=1, le=50)
    after_turn: int | None = Field(default=None, ge=0)
    source: HarvestSource = "auto"
    metadata_only: bool = False
    marker: str | None = None
    successor_birth_id: str | None = None


class HarvestResponse(BaseModel):
    """Harvest outcome with optional turns and separate ack_class field."""

    outcome: HarvestOutcome
    ack_class: AckClass = "no_proof"
    turns: list[CseSessionTurn] = Field(default_factory=list)
    truncated: bool = False
    cursor: int | None = None
    content_provenance: str | None = None
    provenance: dict[str, Any] | None = None
    streaming: bool | None = None
    stop: bool | None = None
    tool_pause: bool | None = None
    reason: str | None = None


class PasteRequest(BaseModel):
    """Mutating paste — identity omission forbidden; authorization gate before DOM."""

    chat_url: str | None = None
    registration_id: str | None = None
    prompt_text: str | None = None
    prompt_uri: str | None = None
    envelope: PasteEnvelope = "free"
    grant: str | None = None
    caller_registration_id: str | None = None
    parent_thread: str | None = None
    superseded_registration_id: str | None = None
    idempotency_key: str | None = None
    min_receipt: MinReceipt = "dom_paste"


class PasteResponse(BaseModel):
    """Paste receipt — never implies ACK, harvest, or release."""

    ok: bool
    send_verified: bool = False
    receipt: str | None = None
    pasted_at: float | None = None
    streaming_at_paste: bool | None = None
    target_binding: str | None = None
    idempotency_key: str | None = None
    replayed: bool = False
    registration_id: str | None = None
    chat_url: str | None = None
    error: str | None = None
    detail: str | None = None
    code: str | None = None
