"""Pydantic models for POST /graph/recall — life-recall G1 card contract."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RecallNull(StrEnum):
    """Closed enum of typed null tokens on a recall card."""

    resolver_miss = "resolver_miss"
    scope_truncated = "scope_truncated"
    vocab_not_covered = "vocab_not_covered"
    nothing_on_record = "nothing_on_record"


class RecallRequest(BaseModel):
    """POST body for matter and continuity recall routes."""

    q: str | None = Field(default=None, description="Free-text or typed entity ref")
    seeds: list[str] | None = Field(default=None, description="Pinned entity refs")

    @model_validator(mode="after")
    def require_q_or_seeds(self) -> RecallRequest:
        q_ok = bool(self.q and self.q.strip())
        seeds_ok = bool(self.seeds and len(self.seeds) > 0)
        if not q_ok and not seeds_ok:
            msg = "At least one of q or seeds is required"
            raise ValueError(msg)
        return self


class ResolvedEntity(BaseModel):
    """A hub or seed entity successfully resolved for recall."""

    entity_id: str
    via: str
    confidence: str | None = None


class RecallCandidate(BaseModel):
    """Ambiguous resolution option — seat re-calls with pinned seeds."""

    entity_id: str
    name: str | None = None
    why_matched: str


class DispositionRow(BaseModel):
    """Terminal-first disposition row from burst/terminal_facts partition."""

    predicate_form: str
    party: str
    disposition_date: str | None = None
    assertion_id: int
    epistemic_state: str | None = None
    machine_derived: bool = False
    hop_distance: int | None = None
    arrival_path: list[str] | None = None


class AssociationRow(BaseModel):
    """Top activate hit — the walk product on the recall card."""

    claim: str
    assertion_id: int
    activation_path: list[str] | None = None
    entrenchment: float | None = None


class RecallDisclosure(BaseModel):
    """Generalized burst-style disclosure for the whole recall card."""

    rows_scanned: int = 0
    rows_returned: int = 0
    drops: int = 0
    caps_hit: bool = False
    scope_truncated: bool = False
    vocabulary_covered: bool = False


class RecallNextAdvisory(BaseModel):
    """Advisory escalation hint — never authority."""

    action: Literal["delegate"] = "delegate"
    op: Literal["investigate"] = "investigate"
    reason: str


class RecallCard(BaseModel):
    """Read-time recall projection — the unit of return for G1 routes."""

    model_config = ConfigDict(populate_by_name=True)

    mode: Literal["matter", "continuity"]
    resolved: list[ResolvedEntity] = Field(default_factory=list)
    candidates: list[RecallCandidate] = Field(default_factory=list)
    dispositions: list[DispositionRow] = Field(default_factory=list)
    associations: list[AssociationRow] = Field(default_factory=list)
    continuity: dict[str, Any] | None = None
    disclosure: RecallDisclosure = Field(default_factory=RecallDisclosure)
    nulls: list[RecallNull] = Field(default_factory=list)
    next_advisory: RecallNextAdvisory | None = Field(default=None, alias="_next")
