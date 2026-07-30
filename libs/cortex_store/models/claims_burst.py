"""Pydantic schemas for POST /claims/burst — salience slice 3+4."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class BurstDropReason(StrEnum):
    """Closed taxonomy of per-row burst drop causes."""

    detector_no_match = "detector_no_match"
    detector_declined_ambiguous = "detector_declined_ambiguous"
    party_underivable = "party_underivable"
    detector_action_unknown = "detector_action_unknown"
    predicate_unparseable = "predicate_unparseable"
    action_out_of_vocabulary = "action_out_of_vocabulary"


BURST_ANOMALY_DROP_REASONS: frozenset[BurstDropReason] = frozenset(
    {
        BurstDropReason.detector_action_unknown,
        BurstDropReason.predicate_unparseable,
    }
)

BURST_DROP_ID_CAP: int = 200


class ClaimsBurstRequest(BaseModel):
    vocabulary: list[str] = Field(
        ...,
        min_length=1,
        description="Controlled action enum terms (e.g. spread_extension)",
    )
    scope_entity_ids: list[str] = Field(
        ...,
        min_length=1,
        description="Entity ids whose assertions are scanned (read-only)",
    )
    include_contradictions: bool = Field(
        default=False,
        description="When true, synthesize request(action, party) probes per vocabulary term",
    )
    mode: Literal["pre_speak"] = Field(
        default="pre_speak",
        description="Burst mode (v0: pre_speak only)",
    )


class BurstClaimItem(BaseModel):
    assertion_id: int
    claim: str
    predicate_form: str
    epistemic_state: str | None
    terminal: bool
    entity_id: str
    functor: str
    action: str
    party: str
    derivation: str | None = None
    claim_excerpt: str | None = None
    hop_distance: int | None = None
    arrival_path: list[str] | None = None
    machine_derived: bool = False
    detector_version: str | None = None
    disposition_date: str | None = None
    undated: bool = False


class ContradictionPairItem(BaseModel):
    proposed_predicate_form: str
    proposed_functor: str
    blocking_assertion_id: int
    blocking_predicate_form: str
    reason: str


class BurstDropGroup(BaseModel):
    """Per-reason drop accounting with capped assertion-id samples."""

    reason: BurstDropReason
    count: int
    assertion_ids: list[int]
    assertion_ids_truncated: bool


class BurstDisclosure(BaseModel):
    """Versioned read-only accounting of burst scope processing."""

    rows_scanned: int
    rows_returned: int
    rows_dropped_total: int
    drops: list[BurstDropGroup]
    vocabulary_requested: list[str]
    vocabulary_accepted: list[str]
    vocabulary_rejected: list[str]
    detector_version: str
    disclosure_version: int


class ClaimsBurstResponse(BaseModel):
    vocabulary: list[str]
    scope_entity_ids: list[str]
    mode: Literal["pre_speak"]
    claims: list[BurstClaimItem]
    contradiction_pairs: list[ContradictionPairItem]
    disclosure: BurstDisclosure = Field(
        ...,
        description="Read-only drop accounting; always present on every response",
    )
