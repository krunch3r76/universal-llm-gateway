"""Pydantic schemas for POST /claims/burst — salience slice 3+4."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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


class ContradictionPairItem(BaseModel):
    proposed_predicate_form: str
    proposed_functor: str
    blocking_assertion_id: int
    blocking_predicate_form: str
    reason: str


class ClaimsBurstResponse(BaseModel):
    vocabulary: list[str]
    scope_entity_ids: list[str]
    mode: Literal["pre_speak"]
    claims: list[BurstClaimItem]
    contradiction_pairs: list[ContradictionPairItem]
