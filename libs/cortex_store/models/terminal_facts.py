"""Terminal facts block — slice 5a entity_get enrich-on-read."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .claims_burst import BurstClaimItem


class TerminalFactsBlock(BaseModel):
    """Terminal-state predicates (denied/granted) imprinted on hub entity reads."""

    facts: list[BurstClaimItem] = Field(default_factory=list)
    cap: int
    capped: bool = False
